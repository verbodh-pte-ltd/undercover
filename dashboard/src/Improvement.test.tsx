import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ConfigVersion, Recipe, Round } from "./api";
import { App } from "./App";
import { dana, fakeBackend, marcus, signIn } from "./fixtures";
import { Improvement } from "./Improvement";

const scores = {
  accuracy: 0.7,
  grade: 1.5,
  cost_per_claim: 0.001,
  seconds_per_claim: 20,
  runs: 2,
};

const working: Round = {
  round_id: 2,
  stage: "first_evaluations",
  recipe_version: 1,
  version_before: 1,
  version: null,
  reason: null,
  kept: null,
  why: null,
  held_out_before: null,
  held_out_after: null,
  proposal: null,
  events: [],
  progress: { started: 2, needed: 3 },
  spent: 0.0123,
  left: 0.4877,
  started_by: "HND-01",
  started_at: "2026-11-01T09:00:00+00:00",
  stopped_by: null,
  finished_at: null,
};

const finished: Round = {
  ...working,
  round_id: 1,
  stage: "finished",
  version: 2,
  reason: "Decline a claim with no receipt",
  kept: true,
  why: "accuracy improved",
  held_out_before: scores,
  held_out_after: { ...scores, accuracy: 0.84, grade: 2.25 },
  progress: null,
  spent: 0.2,
  left: 0.3,
  events: [
    {
      kind: "change",
      decision: "reject",
      handler: "Dana Whitfield",
      reason: "Too broad",
      decided_at: "2026-11-01T08:10:00+00:00",
    },
  ],
};

const waiting: Round = {
  ...working,
  stage: "change_waiting",
  progress: null,
  proposal: {
    kind: "change",
    instructions: "Decline a claim with no receipt.",
    model: "openai/gpt-6-luna",
    effort: "medium",
    reason: "Most misses were approvals with no receipt",
    before: { model: "openai/gpt-6-luna", effort: "low" },
    changed_lines: ["-Approve it.", "+Decline a claim with no receipt."],
  },
};

const v1: ConfigVersion = {
  version: 1,
  instructions: "Approve it.",
  model: "openai/gpt-6-luna",
  effort: "low",
  reason: "The claim agent as it was before versions",
  proposed_by: "seed",
  approved_by: "seed",
  approved_at: "2026-11-01T08:00:00+00:00",
  kept: true,
  current: false,
  held_out: scores,
  before: null,
  changed_lines: null,
};

const v2: ConfigVersion = {
  ...v1,
  version: 2,
  kept: true,
  current: true,
  held_out: { ...scores, accuracy: 0.84, grade: 2.25 },
};

const v3: ConfigVersion = {
  ...v1,
  version: 3,
  kept: false,
  held_out: { ...scores, accuracy: 0.6, grade: null },
};

const recipes: Recipe[] = [
  {
    version: 2,
    text: "Read the results.\nPropose one change.",
    reason: "Smaller steps",
    proposed_by: "improver (round 1)",
    approved_by: "HND-01",
    approved_at: "2026-11-01T08:30:00+00:00",
    changed_lines: ["-Propose a change.", "+Propose one change."],
  },
  {
    version: 1,
    text: "Read the results.\nPropose a change.",
    reason: "The lab's first recipe",
    proposed_by: "seed",
    approved_by: "seed",
    approved_at: "2026-11-01T08:00:00+00:00",
    changed_lines: null,
  },
];

const routes = (rounds: Round[], more: Record<string, unknown> = {}) => ({
  "/api/rounds": rounds,
  "/api/configuration/versions": [v3, v2, v1],
  "/api/recipes": recipes,
  "/api/handlers": [dana, marcus],
  ...more,
});

const posted = (fetchMock: ReturnType<typeof fakeBackend>, path: string) =>
  fetchMock.mock.calls.find(
    (call) =>
      call[0] === path &&
      (call as unknown as [string, RequestInit?])[1]?.method === "POST",
  );

beforeEach(() => {
  signIn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.location.hash = "";
});

it("shows where a working round has got to, and stops it after confirming", async () => {
  const fetchMock = fakeBackend(
    routes([working, finished], { "/api/rounds/2/stop": working }),
  );
  render(<Improvement handler={dana} />);
  const stages = await screen.findByRole("list", { name: "Round 2 stages" });
  const now = within(stages)
    .getAllByRole("listitem")
    .find((li) => li.getAttribute("aria-current") === "step");
  expect(now?.textContent).toContain("First evaluations2 of 3");
  expect(screen.getAllByText("Working")).toHaveLength(2);
  expect(screen.getByText(/Started by Dana Whitfield/)).toBeDefined();
  expect(screen.getByText("$0.5000")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Stop the round" }));
  const dialog = screen.getByRole("dialog", { name: "Stop round 2?" });
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Stop the round" }),
  );
  await waitFor(() => {
    expect(posted(fetchMock, "/api/rounds/2/stop")).toBeDefined();
  });
});

it("shows each stage of a round in turn", async () => {
  const at = async (round: Round, text: string) => {
    fakeBackend(routes([round]));
    render(<Improvement handler={dana} />);
    const stages = await screen.findByRole("list", { name: /stages/ });
    const now = within(stages)
      .getAllByRole("listitem")
      .find((li) => li.getAttribute("aria-current") === "step");
    expect(now?.textContent).toContain(text);
    cleanup();
  };
  await at({ ...working, stage: "improving", progress: null }, "Improver");
  await at(waiting, "Change waiting");
  await at({ ...working, stage: "improving", version: 2 }, "Evaluations");
  await at(
    { ...working, stage: "evaluations", version: 2, progress: null },
    "Evaluations",
  );
  await at({ ...finished, stage: "verdict" }, "Verdict");
  await at({ ...finished, stage: "improving" }, "Recipe waiting");
  await at({ ...finished, stage: "recipe_waiting" }, "Recipe waiting");
});

it("starts a round after confirming", async () => {
  const fetchMock = fakeBackend(
    routes([finished], {
      "/api/rounds/1/stop": finished,
    }),
  );
  render(<Improvement handler={dana} />);
  expect(await screen.findAllByText("Finished")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "Start a round" }));
  const dialog = screen.getByRole("dialog", { name: "Start a round?" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Start a round" }));
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "Start a round",
    }),
  );
  await waitFor(() => {
    expect(posted(fetchMock, "/api/rounds")).toBeDefined();
  });
});

it("shows a stopped round and who stopped it", async () => {
  fakeBackend(
    routes([
      {
        ...working,
        stage: "stopped",
        stopped_by: "HND-01",
        finished_at: "2026-11-01T09:30:00+00:00",
      },
    ]),
  );
  render(<Improvement handler={dana} />);
  expect(await screen.findByText(/Stopped by Dana Whitfield on/)).toBeDefined();
  expect(screen.queryByRole("list", { name: /stages/ })).toBeNull();
});

it("shows a proposal's diff, model and effort, and approves it after confirming", async () => {
  const fetchMock = fakeBackend(
    routes([waiting, finished], { "/api/rounds/2/approve": waiting }),
  );
  render(<Improvement handler={dana} />);
  expect(
    await screen.findByText("A change to the claim agent waits for approval"),
  ).toBeDefined();
  expect(screen.getByText("low to medium")).toBeDefined();
  expect(
    screen.getByText("Most misses were approvals with no receipt"),
  ).toBeDefined();
  const lines = screen.getByRole("list", { name: "Changed lines" });
  expect(lines.querySelector(".added")?.textContent).toContain(
    "Decline a claim with no receipt.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  const dialog = screen.getByRole("dialog", { name: "Approve this change?" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Approve" }));
  await waitFor(() => {
    expect(posted(fetchMock, "/api/rounds/2/approve")).toBeDefined();
  });
});

it("rejects a proposal with the handler's reason", async () => {
  const fetchMock = fakeBackend(
    routes([waiting], {
      "/api/rounds/2/reject": [409, { detail: "Dispatch 4 is running" }],
    }),
  );
  render(<Improvement handler={dana} />);
  fireEvent.change(await screen.findByLabelText(/Your reason/), {
    target: { value: "Too broad" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Dispatch 4 is running",
  );
  const call = posted(fetchMock, "/api/rounds/2/reject") as unknown as [
    string,
    RequestInit,
  ];
  expect(call[1].body).toBe(JSON.stringify({ reason: "Too broad" }));
});

it("shows a proposed recipe as a diff", async () => {
  fakeBackend(
    routes([
      {
        ...finished,
        stage: "recipe_waiting",
        proposal: {
          kind: "recipe",
          recipe: "Read the results.",
          reason: "Shorter",
          changed_lines: ["-Propose a change."],
        },
      },
    ]),
  );
  render(<Improvement handler={dana} />);
  expect(
    await screen.findByText("A new recipe for the improver waits for approval"),
  ).toBeDefined();
  expect(screen.getByText("Changes to the recipe")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  expect(
    screen.getByRole("dialog", { name: "Approve this recipe?" }),
  ).toBeDefined();
});

it("shows Marcus why he can't act on a round", async () => {
  fakeBackend(routes([waiting]));
  render(<Improvement handler={marcus} />);
  expect(
    await screen.findByText(/You can follow the rounds but not start or stop/),
  ).toBeDefined();
  expect(screen.getByText("low to medium")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop the round" })).toBeNull();
});

it("lists the rounds with their scores, verdict and cost, and charts the versions", async () => {
  fakeBackend(routes([working, finished]));
  render(<Improvement handler={dana} />);
  const row = (await screen.findAllByRole("row")).find((r) =>
    r.textContent.startsWith("1"),
  );
  expect(row?.textContent).toContain("Version 2");
  expect(row?.textContent).toContain("70% to 84%");
  expect(row?.textContent).toContain("1.50 to 2.25");
  expect(row?.textContent).toContain("Kept");
  expect(row?.textContent).toContain("accuracy improved");
  expect(row?.textContent).toContain("$0.2000");
  expect(
    screen.getByRole("img", { name: /^Held-out accuracy: version 1 70%/ }),
  ).toBeDefined();
  // Version 3 has no grade, so the grade chart leaves it out.
  expect(
    screen.getByRole("img", { name: /grade/ }).getAttribute("aria-label"),
  ).toBe("Judge's grade, 0 (F) to 3 (A): version 1 1.5, version 2 2.3");
  expect(screen.getByText("Version 3: 60%, rolled back")).toBeDefined();
});

it("opens what a recipe changed, with the handlers' names", async () => {
  fakeBackend(routes([]));
  render(<Improvement handler={dana} />);
  expect(await screen.findByText(/No rounds yet\. Start one/)).toBeDefined();
  expect(screen.getAllByText("Dana Whitfield")).toHaveLength(1);
  expect(screen.getAllByText("Seeded")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Changes in recipe 2" }));
  expect(screen.getByText("Recipe 2 against recipe 1")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Hide changes" }));
  expect(screen.queryByText("Recipe 2 against recipe 1")).toBeNull();
});

it("says when no version has scores yet, and what to do when loading fails", async () => {
  fakeBackend(routes([], { "/api/configuration/versions": [] }));
  render(<Improvement handler={marcus} />);
  expect(
    await screen.findByText(/No version has held-out scores yet/),
  ).toBeDefined();
  expect(screen.getByText(/once a handler starts one/)).toBeDefined();
  cleanup();
  fakeBackend({ "/api/rounds": [500, { detail: "down" }] });
  render(<Improvement handler={dana} />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Check that the backend is running",
  );
});

it("shows where you are in the navigation", async () => {
  fakeBackend(routes([]));
  window.location.hash = "#/improvement";
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Improvement" }),
  ).toBeDefined();
  expect(
    screen
      .getByRole("link", { name: "Improvement" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen.getByRole("link", { name: "Queue" }).getAttribute("aria-current"),
  ).toBeNull();
});

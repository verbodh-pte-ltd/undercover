import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ConfigVersion } from "./api";
import { App } from "./App";
import { Configuration } from "./Configuration";
import { fakeBackend, signIn } from "./fixtures";

const first: ConfigVersion = {
  version: 1,
  instructions: "You decide one claim.\nRead the wording.",
  model: "openai/gpt-6-luna",
  effort: "low",
  reason: "The claim agent as it was before versions",
  proposed_by: "seed",
  approved_by: "seed",
  approved_at: "2026-11-01T08:00:00+00:00",
  kept: true,
  current: false,
  held_out: null,
  before: null,
  changed_lines: null,
};

const second: ConfigVersion = {
  ...first,
  version: 2,
  instructions: "You decide one claim.\nRead the manual too.",
  effort: "medium",
  reason: "A rule for each mistake",
  proposed_by: "improver (round 1)",
  approved_by: "HND-01",
  current: true,
  held_out: {
    accuracy: 0.84,
    grade: 2.25,
    cost_per_claim: 0.0012,
    seconds_per_claim: 12.4,
    runs: 2,
  },
  before: { model: "openai/gpt-6-luna", effort: "low" },
  changed_lines: [
    "-Read the wording.",
    "+Read the manual too.",
    " Decide the claim.",
    "…",
    "+Say why.",
  ],
};

const third: ConfigVersion = {
  ...second,
  version: 3,
  kept: false,
  current: false,
  held_out: null,
  before: { model: "openai/gpt-6-luna", effort: "medium" },
  changed_lines: [],
};

beforeEach(() => {
  signIn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.location.hash = "";
});

it("shows the current version and every version with its held-out scores", async () => {
  fakeBackend({ "/api/configuration/versions": [third, second, first] });
  render(<Configuration />);
  expect(await screen.findByText("Current version: version 2")).toBeDefined();
  expect(screen.getByText(/Read the manual too\./)).toBeDefined();
  // Each version's row, then its reason on a line of its own.
  const all = screen.getAllByRole("row").slice(1);
  const rows = all.filter((_, i) => i % 2 === 0);
  expect(all[1].className).toBe("reason-row");
  expect(all[1].textContent).toBe(third.reason);
  expect(rows.map((r) => r.querySelector(".chip")?.textContent)).toEqual([
    "Rolled back",
    "Current",
    "Kept",
  ]);
  expect(rows[1].textContent).toContain("84%");
  expect(rows[1].textContent).toContain("2.25 of 3");
  expect(rows[1].textContent).toContain("$0.0012");
  expect(rows[1].textContent).toContain("12.4 s");
  expect(rows[2].textContent).toContain("Not scored yet");
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("opens what a version changed from the one before", async () => {
  fakeBackend({ "/api/configuration/versions": [third, second, first] });
  render(<Configuration />);
  expect(
    screen.queryByRole("button", { name: "Changes in version 1" }),
  ).toBeNull();
  fireEvent.click(
    await screen.findByRole("button", { name: "Changes in version 2" }),
  );
  expect(screen.getByText("Version 2 against version 1")).toBeDefined();
  expect(screen.getByText("low to medium")).toBeDefined();
  const lines = screen.getByRole("list", { name: "Changed lines" });
  expect(lines.querySelector(".removed")?.textContent).toContain(
    "Read the wording.",
  );
  expect(lines.querySelector(".added")?.textContent).toContain(
    "Read the manual too.",
  );
  // An unchanged line beside a change, then a mark for the text skipped.
  expect(lines.querySelector(".same")?.textContent).toBe("Decide the claim.");
  expect(lines.querySelector(".skipped")?.textContent).toContain(
    "Unchanged text skipped",
  );
  fireEvent.click(screen.getByRole("button", { name: "Changes in version 3" }));
  expect(
    screen.getByText("The text is the same as the version before."),
  ).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Hide changes" }));
  expect(screen.queryByText("Version 3 against version 2")).toBeNull();
});

it("says what to do when the versions can't be loaded", async () => {
  fakeBackend({ "/api/configuration/versions": [500, { detail: "down" }] });
  render(<Configuration />);
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Check that the backend is running",
  );
});

it("sends the old Instructions address to the Configuration page", async () => {
  fakeBackend({ "/api/configuration/versions": [first] });
  window.location.hash = "#/instructions";
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Configuration" }),
  ).toBeDefined();
  expect(
    screen.getByRole("link", { name: "Configuration" }).getAttribute("href"),
  ).toBe("#/configuration");
});

it("shows who approved each version by name", async () => {
  fakeBackend({
    "/api/configuration/versions": [second, first],
    "/api/handlers": [{ handler_id: "HND-01", name: "Dana Whitfield" }],
  });
  render(<Configuration />);
  expect(await screen.findAllByText(/Dana Whitfield/)).toHaveLength(2);
  expect(screen.getAllByText("Seeded")).toHaveLength(2);
  expect(screen.queryByText("HND-01")).toBeNull();
});

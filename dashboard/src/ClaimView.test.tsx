import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Claim } from "./api";
import { ClaimView, POLL_MS } from "./ClaimView";
import {
  claim,
  claimRoutes,
  dana,
  fakeBackend,
  marcus,
  signIn,
} from "./fixtures";

const step = (id: number, text: string) => ({ id, kind: "tool_call", text });

async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  signIn();
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("shows new steps as the agent works, without a reload", async () => {
  const routes = claimRoutes({ ...claim, status: "in_progress" }, [
    step(1, "get_claim"),
  ]);
  fakeBackend(routes);
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByText("get_claim")).toBeDefined();
  expect(screen.queryByText("get_policy")).toBeNull();

  routes["/api/claims/CLM-1001/steps"] = [
    step(1, "get_claim"),
    step(2, "get_policy"),
  ];
  await wait(POLL_MS);
  expect(screen.getByText("get_policy")).toBeDefined();
});

it("shows the story's date as a date and folds a tool's result to one line", async () => {
  fakeBackend(
    claimRoutes(claim, [
      { id: 1, kind: "tool_result", text: '{"claim_id": "CLM-1001"}' },
    ]),
  );
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByText(/Rome, Italy, Jun 18, 2026/)).toBeDefined();
  const result = screen.getByText('{"claim_id": "CLM-1001"}');
  expect(result.tagName).toBe("SUMMARY");
  expect(result.closest("details")?.open).toBe(false);
});

it("stops checking once the claim is decided", async () => {
  const routes = claimRoutes({ ...claim, status: "in_progress" });
  const fetchMock = fakeBackend(routes);
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);

  routes["/api/claims/CLM-1001"] = {
    ...claim,
    status: "decided",
    decision: "decline",
    payout: 0,
    reason: "Not covered.",
    policy_section: "9. General exclusions",
  };
  await wait(POLL_MS);
  expect(screen.getByText("Not covered.")).toBeDefined();

  const calls = fetchMock.mock.calls.length;
  await wait(POLL_MS * 3);
  expect(fetchMock.mock.calls).toHaveLength(calls);
});

it("signs off a large payout and shows it paid and decided straight away", async () => {
  const waiting: Claim = {
    ...claim,
    status: "waiting_for_sign_off",
    decision: "approve",
    payout: 1250,
    reason: "Covered.",
    policy_section: "7. Emergency Medical",
  };
  const fetchMock = fakeBackend({
    ...claimRoutes(waiting),
    "/api/claims/CLM-1001/sign-off": {
      ...waiting,
      status: "decided",
      signed_off_by: "HND-01",
      signed_off_at: "2026-10-30T06:41:00+00:00",
      paid_amount: 1250,
      paid_at: "2026-10-30T06:41:00+00:00",
    },
  });
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByText("Not paid yet")).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "Sign off $1,250.00" }));
  await wait(0);
  expect(screen.getByText(/^Signed off by Dana Whitfield on /)).toBeDefined();
  expect(screen.getByText(/^Paid \$1,250\.00 on /)).toBeDefined();
  expect(screen.getByText("Decided", { exact: true })).toBeDefined();
  expect(screen.queryByText("Not paid yet")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  expect(fetchMock).toHaveBeenCalledWith("/api/claims/CLM-1001/sign-off", {
    method: "POST",
    headers: { Authorization: "Bearer token" },
  });
});

const held: Claim = {
  ...claim,
  status: "waiting_for_sign_off",
  decision: "approve",
  payout: 1250,
  reason: "Covered.",
  policy_section: "7. Emergency Medical",
};

it("tells a handler who can't sign off over the limit why", async () => {
  fakeBackend(claimRoutes(held));
  render(<ClaimView claimId="CLM-1001" handler={marcus} />);
  await wait(0);
  expect(
    screen.getByText(/You can't sign off payouts over the approval limit/),
  ).toBeDefined();
  expect(screen.queryByRole("button")).toBeNull();
});

it("shows why a sign-off was refused", async () => {
  fakeBackend({
    ...claimRoutes(held),
    "/api/claims/CLM-1001/sign-off": [
      401,
      { detail: "Your sign-in has expired or isn't valid. Sign in again" },
    ],
  });
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  fireEvent.click(screen.getByRole("button", { name: "Sign off $1,250.00" }));
  await wait(0);
  expect(screen.getByRole("alert").textContent).toBe(
    "Your sign-in has expired or isn't valid. Sign in again",
  );
});

it("says the Gateway refused the payment when it did, and the limit otherwise", async () => {
  fakeBackend(claimRoutes({ ...held, held_by_gateway: true }));
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(
    screen.getByText(/^The Gateway refused the agent's payment/),
  ).toBeDefined();
  expect(screen.queryByText(/over the approval limit, so it waits/)).toBeNull();
  cleanup();

  fakeBackend(claimRoutes(held));
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(
    screen.getByText(/^This payout is over the approval limit/),
  ).toBeDefined();
  expect(screen.queryByText(/The Gateway refused/)).toBeNull();
});

const manual = {
  file: "claims_manual.md",
  system: "Claims operations",
  owner: "Head of claims operations",
  reviewed: "2026-09-01",
};
const glossary = {
  file: "glossary.yaml",
  system: "The policy wording, version 2026.1",
  owner: "Legal",
  reviewed: "2026-09-01",
};
const lookup = (id: number, lookedUp: string, files: object[]) => ({
  id,
  kind: "lookup",
  text: `${lookedUp}, from ${files.length.toString()} file`,
  detail: { tool: "read_section", looked_up: lookedUp, files },
});

it("labels each kind of step, with refusals standing out", async () => {
  fakeBackend(
    claimRoutes(claim, [
      { id: 1, kind: "tool_call", text: "get_claim({})", detail: null },
      lookup(2, "the term Unattended", [glossary]),
      {
        id: 3,
        kind: "memory_search",
        text: "search_memory: A giraffe ate his passport.",
        detail: {
          query: "earlier claims",
          found: ["A giraffe ate his passport."],
        },
      },
      {
        id: 4,
        kind: "refusal",
        text: "undercover-app___pay_claim: Refused by the Gateway: Tool Execution Denied",
        detail: { tool: "undercover-app___pay_claim", by: "gateway" },
      },
      {
        id: 5,
        kind: "refusal",
        text: "record_decision: Refused (422): Claims manual section 14",
        detail: { tool: "record_decision", by: "app" },
      },
      {
        id: 6,
        kind: "memory_save",
        text: "remember: Claim CLM-1001 saved",
        detail: { saved: ["Claim CLM-1001"] },
      },
    ]),
  );
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  for (const kind of [
    "Tool call",
    "Lookup",
    "Memory search",
    "Refused by the Gateway",
    "Refused",
    "Memory saved",
  ]) {
    expect(screen.getByText(kind, { exact: true })).toBeDefined();
  }
  const gateway = screen.getByText("Refused by the Gateway");
  expect(gateway.closest("li")?.className).toBe("refusal");
  const found = screen.getByText("search_memory: A giraffe ate his passport.");
  expect(found.tagName).toBe("SUMMARY");
});

it("lists everything looked up once, grouped by file with its source", async () => {
  fakeBackend(
    claimRoutes(claim, [
      lookup(1, "claims_manual section 9", [manual]),
      lookup(2, "claims_manual section 9", [manual]),
      lookup(3, "the term Unattended", [glossary]),
      lookup(4, "claims_manual section 2", [manual]),
    ]),
  );
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByText("Context used")).toBeDefined();
  expect(screen.getAllByText("claims_manual section 9")).toHaveLength(1);
  const file = screen.getByText("claims_manual.md");
  const group = file.closest("li");
  expect(group?.textContent).toContain(
    "Claims operations, owned by Head of claims operations, reviewed Sep 1, 2026",
  );
  expect(group?.textContent).toContain("claims_manual section 2");
  expect(screen.getByText("glossary.yaml")).toBeDefined();
});

it("says nothing was looked up yet", async () => {
  fakeBackend(claimRoutes(claim));
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByText("Nothing yet.")).toBeDefined();
});

it("shows the whole decision record with its calculation line by line", async () => {
  const decided: Claim = {
    ...claim,
    status: "waiting_for_sign_off",
    decision: "approve",
    payout: 1250,
    reason: "The treatment is covered.",
    policy_section: "7. Emergency Medical",
    calculation: {
      lines: [
        { step: "Medical bill", amount: 1300 },
        { step: "Less the deductible", amount: -50 },
      ],
      payout: 1250,
    },
    decided_by: "UnderCover Agent, version 1",
    decided_at: "2026-11-02T10:00:00+00:00",
    manual_version: "2026.2",
  };
  fakeBackend({
    ...claimRoutes(decided),
    "/api/claims/CLM-1001/sign-off": {
      ...decided,
      status: "decided",
      signed_off_by: "HND-01",
      signed_off_at: "2026-11-02T10:05:00+00:00",
      paid_amount: 1250,
      paid_at: "2026-11-02T10:05:00+00:00",
    },
  });
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByText("Medical bill")).toBeDefined();
  expect(screen.getByText("$1,300.00")).toBeDefined();
  expect(screen.getByText("-$50.00")).toBeDefined();
  expect(screen.getByText("Payout")).toBeDefined();
  expect(
    screen.getByText(/^Decided by UnderCover Agent, version 1 on /),
  ).toBeDefined();
  expect(screen.getByText("Claims manual version 2026.2")).toBeDefined();
  expect(
    screen.getByText("Policy section: 7. Emergency Medical"),
  ).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Sign off $1,250.00" }));
  await wait(0);
  expect(screen.getByText(/^Signed off by Dana Whitfield on /)).toBeDefined();
});

const sentBackRecord = {
  decision: "approve" as const,
  payout: 1250,
  calculation: null,
  reason: "The treatment is covered.",
  policy_section: "7. Emergency Medical",
  decided_by: "UnderCover Agent, version 1",
  decided_at: "2026-11-02T10:00:00+00:00",
  note: "Check the receipt: the bill looks like it includes a hotel stay.",
  sent_back_by: "HND-01",
  sent_back_by_name: "Dana Whitfield",
  sent_back_at: "2026-11-02T10:05:00+00:00",
};

it("sends a decision back with a required note, counted to 1,000 characters", async () => {
  const fetchMock = fakeBackend({
    ...claimRoutes(held),
    "/api/claims/CLM-1001/send-back": {
      ...held,
      status: "sent_back",
      decision: null,
      payout: null,
      reason: null,
      policy_section: null,
      sent_back: [sentBackRecord],
    },
  });
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  const sendBack = screen.getByRole("button", { name: "Send back" });
  expect((sendBack as HTMLButtonElement).disabled).toBe(true);
  const note = screen.getByLabelText(/Or send it back to the agent/);
  expect(note.getAttribute("maxlength")).toBe("1000");
  expect(note.hasAttribute("required")).toBe(true);
  expect(screen.getByText("0 of 1,000 characters")).toBeDefined();

  fireEvent.change(note, { target: { value: sentBackRecord.note } });
  expect(screen.getByText("64 of 1,000 characters")).toBeDefined();
  expect((sendBack as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(sendBack);
  await wait(0);
  expect(fetchMock).toHaveBeenCalledWith("/api/claims/CLM-1001/send-back", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer token",
    },
    body: JSON.stringify({ note: sentBackRecord.note }),
  });
  expect(screen.getByText("Sent back", { selector: ".chip" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "Send back" })).toBeNull();
  expect(screen.getByText(/the next dispatch does/)).toBeDefined();
  expect(screen.getByText(sentBackRecord.note)).toBeDefined();
});

it("shows why a send back was refused", async () => {
  fakeBackend({
    ...claimRoutes(held),
    "/api/claims/CLM-1001/send-back": [
      409,
      { detail: "Claim CLM-1001 is not waiting for sign-off" },
    ],
  });
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  fireEvent.change(screen.getByLabelText(/Or send it back/), {
    target: { value: "Look again." },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send back" }));
  await wait(0);
  expect(screen.getByRole("alert").textContent).toBe(
    "Claim CLM-1001 is not waiting for sign-off",
  );
});

it("shows each decision sent back with its note, who and when, beside the current one", async () => {
  fakeBackend(
    claimRoutes({
      ...held,
      payout: 980,
      reason: "Only the treatment is covered.",
      sent_back: [
        { ...sentBackRecord, note: "Second look, please." },
        sentBackRecord,
      ],
    }),
  );
  render(<ClaimView claimId="CLM-1001" handler={dana} />);
  await wait(0);
  expect(screen.getByRole("heading", { name: "Sent back" })).toBeDefined();
  expect(screen.getByText("Only the treatment is covered.")).toBeDefined();
  const notes = screen
    .getAllByText(/^Second look|^Check the receipt/)
    .map((n) => n.textContent);
  expect(notes).toEqual(["Second look, please.", sentBackRecord.note]);
  expect(
    screen.getAllByText(/^Sent back by Dana Whitfield on Nov 2, 2026/),
  ).toHaveLength(2);
  expect(screen.getAllByText("$1,250.00").length).toBeGreaterThan(0);
});

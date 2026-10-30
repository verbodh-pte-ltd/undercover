import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Claim } from "./api";
import {
  claim,
  claimRoutes,
  dispatchRun,
  fakeBackend,
  summary,
} from "./fixtures";

beforeEach(() => {
  window.location.hash = "";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the queue with a count per state", async () => {
  fakeBackend({
    "/api/claims": [
      summary,
      {
        ...summary,
        claim_id: "CLM-1002",
        status: "decided",
        amount_claimed: 1250,
      },
      { ...summary, claim_id: "CLM-1003", status: "needs_handler" },
    ],
    "/api/runs/current": null,
  });
  render(<App />);
  expect(await screen.findByRole("link", { name: "CLM-1001" })).toBeDefined();
  expect(screen.getByText("$1,250.00")).toBeDefined();
  expect(screen.getAllByText("Trip delay")).toHaveLength(3);
  expect(screen.getAllByText("Waiting")).toHaveLength(2);
  expect(screen.getAllByText("Decided")).toHaveLength(2);
  expect(screen.getAllByText("Left for a handler")).toHaveLength(2);
  expect(screen.queryByText("Current run")).toBeNull();
});

it("shows the current dispatch, its claims and its summary", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": dispatchRun,
  });
  render(<App />);
  expect(await screen.findByText("Dispatch 4")).toBeDefined();
  expect(
    screen.getByText(/Finished, 3 claim agents at once, openai\/gpt-6-luna/),
  ).toBeDefined();
  expect(screen.getByText(/model cost \$0\.0123/)).toBeDefined();
  expect(screen.getByText(/run time 12\.0 s/)).toBeDefined();
  expect(screen.getByText("1 approved, 1 declined, 0 referred")).toBeDefined();
  expect(screen.getByText(/waits for sign-off on \$1,250\.00/)).toBeDefined();
  expect(screen.getByText(/is left for a handler/)).toBeDefined();
  expect(screen.getByText(/is still in progress/)).toBeDefined();
});

it("shows a dispatch that has started no claims yet", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": {
      ...dispatchRun,
      status: "running",
      claims: [],
      run_seconds: null,
    },
  });
  render(<App />);
  expect(await screen.findByText("No claims started yet.")).toBeDefined();
  expect(screen.queryByText(/approved,/)).toBeNull();
});

it("starts a dispatch with the chosen number of claim agents", async () => {
  const fetchMock = fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": null,
    "/api/dispatches": { status: "started" },
  });
  render(<App />);
  const picker = await screen.findByLabelText("Claim agents at once");
  expect(picker).toHaveProperty("value", "3");
  fireEvent.change(picker, { target: { value: "25" } });
  fireEvent.click(screen.getByRole("button", { name: "Start dispatch" }));
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith("/api/dispatches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ n: 25 }),
    });
  });
  expect(screen.queryByRole("alert")).toBeNull();
});

it("says when the agent's service isn't running", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": null,
    "/api/dispatches": [503, { detail: "The agent's service isn't running" }],
  });
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start dispatch" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "The agent's service isn't running",
  );
});

it("says why a start was refused when it has no reason", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": null,
    "/api/dispatches": [422, { detail: [{ msg: "too many" }] }],
  });
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start dispatch" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Starting returned 422",
  );
});

it("shows a claim in full before it is decided", async () => {
  fakeBackend(claimRoutes(claim));
  window.location.hash = "#/claims/CLM-1001";
  render(<App />);
  expect(
    await screen.findByText("My flight home from Rome was canceled."),
  ).toBeDefined();
  expect(
    screen.getByText("The agent has not decided this claim yet."),
  ).toBeDefined();
  expect(screen.getByText("Total $245.00.")).toBeDefined();
  expect(await screen.findByText("Premium")).toBeDefined();
  expect(await screen.findByText(/Bag arrived late/)).toBeDefined();
  expect(screen.getByText("No steps yet.")).toBeDefined();
});

it("shows the decision, its reasons and the steps", async () => {
  const decided: Claim = {
    ...claim,
    status: "decided",
    decision: "approve",
    payout: 420,
    paid_amount: 420,
    reason: "The delay is covered.",
    policy_section: "3. Trip Delay",
    sections_read: ["claims_manual 6", "policy_wording 3"],
  };
  const steps = [
    { id: 1, kind: "tool_call", text: 'get_claim({"claim_id": "CLM-1001"})' },
    { id: 2, kind: "reply", text: "Approved, $420.00." },
  ];
  fakeBackend(claimRoutes(decided, steps));
  window.location.hash = "#/claims/CLM-1001";
  render(<App />);
  expect(await screen.findByText("The delay is covered.")).toBeDefined();
  expect(screen.getByText("Policy section: 3. Trip Delay")).toBeDefined();
  expect(screen.getByText("Paid $420.00")).toBeDefined();
  expect(await screen.findByText("Approved, $420.00.")).toBeDefined();
  expect(screen.getByText("Claims manual, section 6")).toBeDefined();
  expect(screen.getByText("Policy wording, section 3")).toBeDefined();
});

it("shows the claim agent's instructions, read only", async () => {
  fakeBackend({ "/api/instructions": { claim: "You decide one claim." } });
  window.location.hash = "#/instructions";
  render(<App />);
  expect(await screen.findByText("You decide one claim.")).toBeDefined();
  expect(screen.getByText("Working a claim")).toBeDefined();
  expect(screen.queryByText("Running the queue")).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("follows the hash from the queue to a claim", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": null,
    ...claimRoutes(claim),
  });
  render(<App />);
  await screen.findByRole("link", { name: "CLM-1001" });
  window.location.hash = "#/claims/CLM-1001";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  expect(
    await screen.findByText("My flight home from Rome was canceled."),
  ).toBeDefined();
});

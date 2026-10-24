import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Claim } from "./api";
import { claim, claimRoutes, fakeBackend, summary } from "./fixtures";

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

it("shows the current run's to-do list and summary", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": {
      run_id: 1,
      cap: 5,
      claims_started: ["CLM-1001", "CLM-1002"],
      summary: "Two claims decided.",
      todos: [
        { claim_id: "CLM-1001", text: "Decide the delay", state: "done" },
        { claim_id: "CLM-1002", text: "Decide the bag", state: "in_progress" },
        { claim_id: "CLM-1003", text: "Decide the passport", state: "to_do" },
        {
          claim_id: "CLM-1004",
          text: "Decide the medical claim",
          state: "left_for_handler",
        },
      ],
    },
  });
  render(<App />);
  expect(await screen.findByText("Current run")).toBeDefined();
  expect(screen.getByText(/2 of 5 claims started, finished/)).toBeDefined();
  expect(screen.getByText("Decide the bag")).toBeDefined();
  expect(screen.getByText("Done")).toBeDefined();
  expect(screen.getByText("To do")).toBeDefined();
  // once in the counts per state and once on the to-do item
  expect(screen.getAllByText("Left for a handler")).toHaveLength(2);
  expect(screen.getByText("Two claims decided.")).toBeDefined();
});

it("shows a run the agent has not planned yet", async () => {
  fakeBackend({
    "/api/claims": [summary],
    "/api/runs/current": {
      run_id: 1,
      cap: 5,
      claims_started: [],
      summary: null,
      todos: [],
    },
  });
  render(<App />);
  expect(
    await screen.findByText("The agent has not planned this run yet."),
  ).toBeDefined();
  expect(screen.getByText("0 of 5 claims started")).toBeDefined();
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

it("shows the instructions for the queue and for a claim, read only", async () => {
  fakeBackend({
    "/api/instructions": {
      queue: "You run the claims queue.",
      claim: "You decide one claim.",
    },
  });
  window.location.hash = "#/instructions";
  render(<App />);
  expect(await screen.findByText("You run the claims queue.")).toBeDefined();
  expect(screen.getByText("You decide one claim.")).toBeDefined();
  expect(screen.getByText("Running the queue")).toBeDefined();
  expect(screen.getByText("Working a claim")).toBeDefined();
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

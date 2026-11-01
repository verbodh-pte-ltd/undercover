import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Claim, EvaluationSummary } from "./api";
import {
  claim,
  claimRoutes,
  evaluation,
  fakeBackend,
  signIn,
} from "./fixtures";

beforeEach(() => {
  signIn();
  window.location.hash = "#/scoreboard";
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const listed: EvaluationSummary = { ...evaluation };

it("lists the evaluations with every measure", async () => {
  fakeBackend({ "/api/evaluations": [listed] });
  render(<App />);
  expect(
    await screen.findByRole("link", { name: "Evaluation 7" }),
  ).toBeDefined();
  // once in the table and once in the form's choice of set
  expect(screen.getAllByText("Held-out set")).toHaveLength(2);
  expect(screen.getByText("Tiny")).toBeDefined();
  expect(screen.getAllByText("50%")).toHaveLength(2);
  expect(screen.getByText("$1,500.00")).toBeDefined();
  expect(screen.getByText("100%")).toBeDefined();
  expect(screen.getByText("$0.0021")).toBeDefined();
  expect(screen.getByText("8.3 s")).toBeDefined();
  expect(screen.getByText("10.5 s")).toBeDefined();
  expect(screen.getByText("Effort low")).toBeDefined();
  expect(screen.getByText("2.60 of 3")).toBeDefined();
});

it("shows an evaluation with no graded claims as not graded", async () => {
  fakeBackend({ "/api/evaluations": [{ ...listed, grade: null }] });
  render(<App />);
  expect(await screen.findByText("Not graded")).toBeDefined();
});

it("says when there are no evaluations yet", async () => {
  fakeBackend({ "/api/evaluations": [] });
  render(<App />);
  expect(await screen.findByText("No evaluations yet.")).toBeDefined();
});

it("starts an evaluation of the chosen set", async () => {
  const fetchMock = fakeBackend({ "/api/evaluations": [] });
  render(<App />);
  fireEvent.change(await screen.findByLabelText("Set"), {
    target: { value: "held_out" },
  });
  fireEvent.click(screen.getByLabelText("Only its five tiny claims"));
  fireEvent.change(screen.getByLabelText("Claim agents at once"), {
    target: { value: "5" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith("/api/evaluations", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer token",
      },
      body: JSON.stringify({ n: 5, eval_set: "held_out", tiny: true }),
    });
  });
});

it("shows an evaluation's claims beside the handlers', with misses marked", async () => {
  fakeBackend({ "/api/evaluations/7": evaluation });
  window.location.hash = "#/scoreboard/7";
  render(<App />);
  const missed = await screen.findByRole("link", { name: "E7-CLM-1102" });
  expect(missed.getAttribute("href")).toBe("#/claims/E7-CLM-1102");
  expect(missed.closest("tr")?.className).toBe("miss");
  const right = screen.getByRole("link", { name: "E7-CLM-1101" });
  expect(right.closest("tr")?.className).toBe("");
  expect(screen.getByText("Miss")).toBeDefined();
  expect(screen.getByText("Right")).toBeDefined();
  expect(
    screen.getByText(/1 missed, cost of mistakes \$1,500\.00/),
  ).toBeDefined();
});

it("shows each claim's grade, its letter and the two checks as yes or no", async () => {
  fakeBackend({ "/api/evaluations/7": evaluation });
  window.location.hash = "#/scoreboard/7";
  render(<App />);
  const graded = (
    await screen.findByRole("link", { name: "E7-CLM-1101" })
  ).closest("tr");
  const cells = [...(graded?.querySelectorAll("td") ?? [])].map(
    (td) => td.textContent,
  );
  expect(cells).toContain("A 2.60");
  expect(cells.slice(-5, -2)).toEqual(["A 2.60", "Yes", "No"]);
  const ungraded = screen
    .getByRole("link", { name: "E7-CLM-1102" })
    .closest("tr");
  expect(ungraded?.textContent).toContain("Not graded");
  expect(
    screen.getByText(/Grade 2\.60 of 3, judge's cost \$0\.0008\./),
  ).toBeDefined();
});

it("shows no sign-off form on an evaluation's copy", async () => {
  const copy: Claim = {
    ...claim,
    status: "waiting_for_sign_off",
    decision: "approve",
    payout: 1250,
    run_id: 7,
    copied_from: "CLM-1001",
  };
  fakeBackend(claimRoutes(copy));
  window.location.hash = "#/claims/CLM-1001";
  render(<App />);
  expect(
    await screen.findByText(/A copy of CLM-1001 for evaluation 7/),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: /Sign off/ })).toBeNull();
});

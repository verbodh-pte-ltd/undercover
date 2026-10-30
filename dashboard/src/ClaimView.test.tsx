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
import { claim, claimRoutes, fakeBackend } from "./fixtures";

const step = (id: number, text: string) => ({ id, kind: "tool_call", text });

async function wait(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("shows new steps as the agent works, without a reload", async () => {
  const routes = claimRoutes({ ...claim, status: "in_progress" }, [
    step(1, "get_claim"),
  ]);
  fakeBackend(routes);
  render(<ClaimView claimId="CLM-1001" />);
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

it("stops checking once the claim is decided", async () => {
  const routes = claimRoutes({ ...claim, status: "in_progress" });
  const fetchMock = fakeBackend(routes);
  render(<ClaimView claimId="CLM-1001" />);
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

it("signs off a large payout, then shows it paid once the agent pays", async () => {
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
      status: "in_progress",
      signed_off_by: "HND-01",
      signed_off_at: "2026-10-30T06:41:00+00:00",
    },
  });
  render(<ClaimView claimId="CLM-1001" />);
  await wait(0);
  expect(screen.getByText("Not paid yet")).toBeDefined();
  expect(screen.getByLabelText("Handler")).toHaveProperty("value", "HND-01");

  fireEvent.click(screen.getByRole("button", { name: "Sign off $1,250.00" }));
  await wait(0);
  expect(screen.getByText(/^Signed off by Dana Whitfield on /)).toBeDefined();
  expect(screen.getByText("Not paid yet")).toBeDefined();
  expect(screen.queryByRole("button")).toBeNull();

  fakeBackend({
    ...claimRoutes({
      ...waiting,
      status: "decided",
      signed_off_by: "HND-01",
      signed_off_at: "2026-10-30T06:41:00+00:00",
      paid_amount: 1250,
      paid_at: "2026-10-30T06:41:03+00:00",
    }),
  });
  await wait(POLL_MS);
  expect(screen.getByText(/^Paid \$1,250\.00 on /)).toBeDefined();
  expect(fetchMock).toHaveBeenCalledWith("/api/claims/CLM-1001/sign-off", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ handler_id: "HND-01" }),
  });
});

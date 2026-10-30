import { vi } from "vitest";
import type { Claim, ClaimSummary, Evaluation, Run } from "./api";

export const summary: ClaimSummary = {
  claim_id: "CLM-1001",
  summary: "Flight home canceled",
  claim_type: "trip_delay",
  amount_claimed: 420,
  customer_name: "Maya Patel",
  status: "waiting",
  decision: null,
  payout: null,
  paid_amount: null,
};

export const claim: Claim = {
  ...summary,
  policy_id: "POL-2001",
  customer_id: "CUS-3001",
  submitted_on: "2026-06-24",
  incident_date: "2026-06-18",
  incident_location: "Rome, Italy",
  story: "My flight home from Rome was canceled.",
  items: [
    { description: "Airport hotel", category: "accommodation", amount: 245 },
  ],
  documents: [
    { name: "Hotel receipt", type: "receipt", content: "Total $245.00." },
  ],
  reason: null,
  policy_section: null,
  signed_off_by: null,
  signed_off_at: null,
  paid_at: null,
  run_id: null,
  copied_from: null,
  cost: 0,
  seconds: 0,
  attempts: 0,
  sections_read: [],
};

export const policy = {
  policy_id: "POL-2001",
  plan: "premium",
  add_ons: [],
  purchased_on: "2026-04-22",
  trip: {
    destination: "Rome, Italy",
    departs_on: "2026-06-10",
    returns_on: "2026-06-18",
    trip_cost: 3100,
  },
  travelers: ["Maya Patel"],
};

export const history = [
  {
    claim_id: "CLM-0412",
    filed_on: "2024-08-19",
    claim_type: "baggage_delay",
    summary: "Bag arrived late",
    outcome: "approved",
    amount_paid: 140,
  },
];

// A route's value is the JSON it answers, or [status, JSON] for a refusal.
export function fakeBackend(routes: Record<string, unknown>) {
  const fetchMock = vi.fn((path: string) => {
    if (!(path in routes))
      return Promise.resolve(new Response("", { status: 404 }));
    const route = routes[path];
    if (Array.isArray(route) && typeof route[0] === "number")
      return Promise.resolve(
        new Response(JSON.stringify(route[1]), { status: route[0] }),
      );
    return Promise.resolve(new Response(JSON.stringify(route)));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export const dispatchRun: Run = {
  run_id: 4,
  kind: "dispatch",
  n: 3,
  model: "openai/gpt-6-luna",
  effort: "low",
  status: "finished",
  started_at: "2026-10-30T08:00:00+00:00",
  finished_at: "2026-10-30T08:00:12+00:00",
  claims: [
    {
      claim_id: "CLM-1001",
      status: "decided",
      decision: "approve",
      payout: 420,
      cost: 0.005,
      seconds: 6,
    },
    {
      claim_id: "CLM-1002",
      status: "decided",
      decision: "decline",
      payout: 0,
      cost: 0.004,
      seconds: 5,
    },
    {
      claim_id: "CLM-1003",
      status: "waiting_for_sign_off",
      decision: "approve",
      payout: 1250,
      cost: 0.0033,
      seconds: 4,
    },
  ],
  cost: 0.0123,
  seconds: 15,
  run_seconds: 12,
  summary: {
    approved: 1,
    declined: 1,
    referred: 0,
    waiting_for_sign_off: [{ claim_id: "CLM-1003", payout: 1250 }],
    left_for_handler: ["CLM-1004"],
    in_progress: ["CLM-1005"],
  },
};

export const evaluation: Evaluation = {
  run_id: 7,
  eval_set: "held_out",
  tiny: true,
  n: 5,
  model: "openai/gpt-6-luna",
  effort: "low",
  status: "finished",
  started_at: "2026-10-30T09:00:00+00:00",
  decision_accuracy: 0.5,
  accuracy: 0.5,
  cost_of_mistakes: 1500,
  straight_through: 1,
  cost_per_claim: 0.0021,
  seconds_per_claim: 8.25,
  run_seconds: 10.5,
  claims: [
    {
      claim_id: "E7-CLM-1101",
      copied_from: "CLM-1101",
      status: "decided",
      agent: "approve",
      agent_payout: 310,
      reason: "Covered.",
      handler: "approve",
      handler_payout: 310,
      decision_right: true,
      right: true,
      mistake_cost: 0,
      cost: 0.002,
      seconds: 8,
    },
    {
      claim_id: "E7-CLM-1102",
      copied_from: "CLM-1102",
      status: "decided",
      agent: "decline",
      agent_payout: 0,
      reason: "Excluded.",
      handler: "approve",
      handler_payout: 640,
      decision_right: false,
      right: false,
      mistake_cost: 1500,
      cost: 0.0022,
      seconds: 8.5,
    },
  ],
};

export const claimRoutes = (loaded: Claim, steps: unknown[] = []) => ({
  "/api/claims/CLM-1001": loaded,
  "/api/claims/CLM-1001/steps": steps,
  "/api/policies/POL-2001": policy,
  "/api/customers/CUS-3001/claims": history,
  "/api/handlers": [
    {
      handler_id: "HND-01",
      name: "Dana Whitfield",
      role: "Senior Claims Handler",
    },
  ],
});

import { vi } from "vitest";
import type { Claim, ClaimSummary } from "./api";

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

export function fakeBackend(routes: Record<string, unknown>) {
  const fetchMock = vi.fn((path: string) => {
    if (!(path in routes))
      return Promise.resolve(new Response("", { status: 404 }));
    return Promise.resolve(new Response(JSON.stringify(routes[path])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

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

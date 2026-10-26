export type Status =
  | "waiting"
  | "in_progress"
  | "waiting_for_sign_off"
  | "decided"
  | "needs_handler";

export type Decision = "approve" | "decline" | "refer";

export interface ClaimSummary {
  claim_id: string;
  summary: string;
  claim_type: string;
  amount_claimed: number;
  customer_name: string;
  status: Status;
  decision: Decision | null;
  payout: number | null;
  paid_amount: number | null;
}

export interface Item {
  description: string;
  category: string;
  amount: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface Claim extends ClaimSummary {
  policy_id: string;
  customer_id: string;
  submitted_on: string;
  incident_date: string;
  incident_location: string;
  story: string;
  items: Item[];
  documents: ClaimDocument[];
  reason: string | null;
  policy_section: string | null;
  signed_off_by: string | null;
  attempts: number;
  sections_read: string[];
}

export interface Policy {
  policy_id: string;
  plan: string;
  add_ons: string[];
  purchased_on: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost: number;
  };
  travelers: string[];
}

export interface PastClaim {
  claim_id: string;
  filed_on: string;
  claim_type: string;
  summary: string;
  outcome: string;
  amount_paid: number;
}

export interface Step {
  id: number;
  kind: "tool_call" | "tool_result" | "reply" | "error";
  text: string;
}

export type TodoState = "to_do" | "in_progress" | "done" | "left_for_handler";

export interface Todo {
  claim_id: string;
  text: string;
  state: TodoState;
}

export interface Run {
  run_id: number;
  cap: number;
  todos: Todo[];
  summary: string | null;
  claims_started: string[];
}

export interface Instructions {
  queue: string;
  claim: string;
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok)
    throw new Error(`${path} returned ${String(response.status)}`);
  return (await response.json()) as T;
}

export const listClaims = () => getJson<ClaimSummary[]>("/api/claims");
export const getClaim = (id: string) => getJson<Claim>(`/api/claims/${id}`);
export const getPolicy = (id: string) => getJson<Policy>(`/api/policies/${id}`);
export const getClaimHistory = (id: string) =>
  getJson<PastClaim[]>(`/api/customers/${id}/claims`);
export const listSteps = (id: string) =>
  getJson<Step[]>(`/api/claims/${id}/steps`);
export const listHandlers = () => getJson<Handler[]>("/api/handlers");
export const getCurrentRun = () => getJson<Run | null>("/api/runs/current");
export const getInstructions = () => getJson<Instructions>("/api/instructions");

export async function signOff(claimId: string, handlerId: string) {
  const response = await fetch(`/api/claims/${claimId}/sign-off`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ handler_id: handlerId }),
  });
  if (!response.ok)
    throw new Error(`Sign-off returned ${String(response.status)}`);
  return (await response.json()) as Claim;
}

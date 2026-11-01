import { supabase } from "./supabase";

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
  signed_off_at: string | null;
  paid_at: string | null;
  attempts: number;
  sections_read: string[];
  run_id: number | null;
  copied_from: string | null;
  cost: number;
  seconds: number;
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

export interface RunClaim {
  claim_id: string;
  status: Status;
  decision: Decision | null;
  payout: number | null;
  cost: number;
  seconds: number;
}

export interface RunSummary {
  approved: number;
  declined: number;
  referred: number;
  waiting_for_sign_off: { claim_id: string; payout: number }[];
  left_for_handler: string[];
  in_progress: string[];
}

export type RunStatus = "running" | "finished" | "stopped";

export interface Run {
  run_id: number;
  kind: "dispatch" | "evaluation";
  n: number;
  model: string;
  effort: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  claims: RunClaim[];
  cost: number;
  seconds: number;
  run_seconds: number | null;
  summary: RunSummary;
}

export type EvalSet = "evaluation" | "held_out";

export interface Measures {
  decision_accuracy: number;
  accuracy: number;
  cost_of_mistakes: number;
  straight_through: number;
  cost_per_claim: number;
  seconds_per_claim: number;
  run_seconds: number | null;
}

export interface EvaluationSummary extends Measures {
  run_id: number;
  eval_set: EvalSet;
  tiny: boolean;
  n: number;
  model: string;
  effort: string;
  status: RunStatus;
  started_at: string;
}

export interface EvaluationClaim {
  claim_id: string;
  copied_from: string;
  status: Status;
  agent: Decision;
  agent_payout: number;
  reason: string | null;
  handler: Decision;
  handler_payout: number;
  decision_right: boolean;
  right: boolean;
  mistake_cost: number;
  cost: number;
  seconds: number;
}

export interface Evaluation extends EvaluationSummary {
  claims: EvaluationClaim[];
}

export interface Instructions {
  claim: string;
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
  email: string;
  can_approve_over_limit: boolean;
}

// Another handler, as the handler list shows them: no email address.
export type Teammate = Omit<Handler, "email">;

// The noise or the tolerance on each measure.
export interface Margins {
  accuracy: number;
  grade: number;
  cost_per_claim: number;
  seconds_per_claim: number;
}

export interface SettingValues {
  approval_limit: number;
  objectives: { accuracy: number; grade: number };
  noise: Margins;
  tolerance: Margins;
  round_spending_limit: number;
  agents_at_once: number;
}

export interface AllowedModel {
  model: string;
  reasoning_efforts: string[];
  input_price: number;
  output_price: number;
}

export interface Settings {
  settings: SettingValues;
  models_allowed: AllowedModel[];
  changes: Partial<
    Record<keyof SettingValues, { handler: string; changed_at: string }>
  >;
}

// The signed-in handler's access token, which goes with every action.
async function authorization() {
  const { data } = await supabase.auth.getSession();
  return { Authorization: `Bearer ${data.session?.access_token ?? ""}` };
}

async function refusal(response: Response, fallback: string) {
  const { detail } = (await response.json()) as { detail: unknown };
  return typeof detail === "string"
    ? detail
    : `${fallback} returned ${String(response.status)}`;
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
export const getSettings = () => getJson<Settings>("/api/settings");
export const getCurrentRun = () => getJson<Run | null>("/api/runs/current");
export const getInstructions = () => getJson<Instructions>("/api/instructions");
export const listEvaluations = () =>
  getJson<EvaluationSummary[]>("/api/evaluations");
export const getEvaluation = (id: string) =>
  getJson<Evaluation>(`/api/evaluations/${id}`);

export async function getSignedInHandler() {
  const response = await fetch("/api/handlers/me", {
    headers: await authorization(),
  });
  if (!response.ok) throw new Error(await refusal(response, "Sign-in"));
  return (await response.json()) as Handler;
}

export async function listHandlers() {
  const response = await fetch("/api/handlers", {
    headers: await authorization(),
  });
  if (!response.ok) throw new Error(await refusal(response, "Handlers"));
  return (await response.json()) as Teammate[];
}

// Saves every setting as the signed-in handler, or throws the reason it was refused.
export async function changeSettings(values: SettingValues) {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...(await authorization()),
    },
    body: JSON.stringify(values),
  });
  if (!response.ok) throw new Error(await refusal(response, "Saving"));
  return (await response.json()) as Settings;
}

async function post(path: string, body: unknown): Promise<string | null> {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await authorization()),
    },
    body: JSON.stringify(body),
  });
  return response.ok ? null : refusal(response, "Starting");
}

// Each returns null once started, or the reason it was refused.
export const startDispatch = (n: number) => post("/api/dispatches", { n });
export const startEvaluation = (n: number, evalSet: EvalSet, tiny: boolean) =>
  post("/api/evaluations", { n, eval_set: evalSet, tiny });

// Signs off as the signed-in handler, or throws the reason it was refused.
export async function signOff(claimId: string) {
  const response = await fetch(`/api/claims/${claimId}/sign-off`, {
    method: "POST",
    headers: await authorization(),
  });
  if (!response.ok) throw new Error(await refusal(response, "Sign-off"));
  return (await response.json()) as Claim;
}

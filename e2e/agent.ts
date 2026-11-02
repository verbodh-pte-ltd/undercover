import type { APIRequestContext } from "@playwright/test";
import { DANA, PASSWORD } from "./sign-in";

// The test backend's fixed API key, which playwright.config.ts gives it. The specs send it
// on the calls they make as the agent's side would.
export const API_KEY = "e2e-agent-key-of-at-least-32-characters";
export const AGENT = { "X-API-Key": API_KEY };

// A handler's sign-in for a read the specs make outside the page, from the stand-in for
// Supabase Auth.
export async function asHandler(request: APIRequestContext, email = DANA) {
  const response = await request.post(
    "http://127.0.0.1:8004/auth/v1/token?grant_type=password",
    { data: { email, password: PASSWORD } },
  );
  const { access_token } = (await response.json()) as { access_token: string };
  return { Authorization: `Bearer ${access_token}` };
}

export interface DecisionBody {
  decision: "approve" | "decline" | "refer";
  payout: number;
  reason: string;
  policy_section: string;
}

const medical = (amount: number) => ({
  benefit: "emergency_medical",
  lines: [
    {
      description: "Medical bill",
      amount,
      quantity: 1,
      kind: "expense",
      nights: 0,
      hours: 0,
    },
  ],
  paid_by_others: 0,
});

// Record a decision as the agent does: an approval first gets a calculation that comes to
// its payout, from one medical bill of the payout plus the plan's deductible, which a first
// calculation of the payout alone shows; and the record names who made it.
export async function recordDecision(
  request: APIRequestContext,
  claimId: string,
  body: DecisionBody,
) {
  if (body.decision === "approve") {
    const path = `/api/claims/${claimId}/payout`;
    const alone = await request.post(path, {
      headers: AGENT,
      data: medical(body.payout),
    });
    const { payout } = (await alone.json()) as { payout: number };
    await request.post(path, {
      headers: AGENT,
      data: medical(2 * body.payout - payout),
    });
  }
  return request.post(`/api/claims/${claimId}/decision`, {
    headers: AGENT,
    data: { ...body, decided_by: "UnderCover Agent, version 1" },
  });
}

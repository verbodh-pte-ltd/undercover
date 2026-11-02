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

// The Copilot's conversations, their turns and each turn's steps, as the app keeps them.
import { authorization, getJson, refusal } from "./api";

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
}

export type TurnState =
  "working" | "waiting_for_confirmation" | "done" | "failed" | "stopped";

// What a waiting confirmation shows, filled by the app from its records.
export interface ConfirmationCard {
  claim_id: string;
  customer_name: string;
  amount: number;
  reason: string;
}

export type CopilotStepKind =
  "claims" | "customers" | "context" | "work" | "memory" | "action";

export type CopilotStepState = "working" | "done" | "refused" | "failed";

// What a step was asked and what came back, trimmed by the Copilot, with a context-layer
// answer's file and owner.
export interface CopilotStepDetail {
  asked: string | null;
  answer: string | null;
  file: string | null;
  owner: string | null;
}

export interface CopilotStep {
  key: string;
  kind: CopilotStepKind;
  tool: string;
  input: string;
  state: CopilotStepState;
  seconds: number | null;
  detail: CopilotStepDetail | null;
  started_at: string;
}

export interface Turn {
  id: number;
  message: string;
  state: TurnState;
  answer: string | null;
  model_calls: number;
  cost: number;
  seconds: number | null;
  card: ConfirmationCard | null;
  error: string | null;
  started_at: string;
  answered_at: string | null;
  steps: CopilotStep[];
}

export interface ConversationDetail extends Conversation {
  turns: Turn[];
}

export type ConfirmationAnswer = "sign_off" | "cancel";

// Messages and confirmation answers renew the sign-in first when less than this is left on it.
export const RENEW_WITHIN_MS = 20 * 60_000;

async function postTurn(path: string, body: unknown, fallback: string) {
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await authorization(RENEW_WITHIN_MS)),
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await refusal(response, fallback));
  return (await response.json()) as Turn;
}

export const listConversations = () =>
  getJson<Conversation[]>("/api/conversations");
export const getConversation = (id: string) =>
  getJson<ConversationDetail>(`/api/conversations/${id}`);

export async function createConversation() {
  const response = await fetch("/api/conversations", {
    method: "POST",
    headers: await authorization(),
  });
  if (!response.ok) throw new Error(await refusal(response, "Conversation"));
  return (await response.json()) as Conversation;
}

// Each throws the reason it was refused, such as a turn still working.
export const sendMessage = (id: string, message: string) =>
  postTurn(`/api/conversations/${id}/messages`, { message }, "Message");
export const answerConfirmation = (id: string, answer: ConfirmationAnswer) =>
  postTurn(`/api/conversations/${id}/confirmation`, { answer }, "Answer");

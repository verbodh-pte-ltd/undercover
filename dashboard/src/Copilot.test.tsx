import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Copilot, POLL_MS } from "./Copilot";
import type {
  Conversation,
  ConversationDetail,
  CopilotStep,
  Turn,
} from "./copilotApi";
import { dana, session, signIn } from "./fixtures";
import { supabase } from "./supabase";

const ID = "6f1c2d4e-0000-4000-8000-000000000001";
const PATH = `/api/conversations/${ID}`;

const conversation: Conversation = {
  id: ID,
  title: "What needs me today?",
  created_at: "2026-11-04T09:00:00+00:00",
};

const step: CopilotStep = {
  key: "s1",
  kind: "claims",
  tool: "get_claim",
  input: "CLM-1012",
  state: "done",
  seconds: 1.24,
  detail: {
    asked: "get_claim CLM-1012",
    answer: '{"claim_id": "CLM-1012"}',
    file: null,
    owner: null,
  },
  started_at: "2026-11-04T09:00:01+00:00",
};

const turn: Turn = {
  id: 1,
  message: "What needs me today?",
  state: "done",
  answer: "Nothing needs you.",
  model_calls: 2,
  cost: 0.0042,
  seconds: 6.5,
  card: null,
  error: null,
  started_at: "2026-11-04T09:00:00+00:00",
  answered_at: null,
  steps: [],
};

const detail = (...turns: Turn[]): ConversationDetail => ({
  ...conversation,
  turns,
});

// Replies given in turn, the last one again once the others are used.
class Replies {
  constructor(readonly list: unknown[]) {}
}

// Stands in for the app: each "METHOD path" gets its reply, or the next of its Replies. A
// [status, body] pair is a refusal.
function backend(routes: Record<string, unknown>) {
  const queues: Record<string, unknown[]> = {};
  const fetchMock = vi.fn((path: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${path}`;
    const all: Record<string, unknown> = {
      "GET /api/conversations": [],
      ...routes,
    };
    if (!(key in all))
      return Promise.resolve(new Response("", { status: 404 }));
    let reply = all[key];
    if (reply instanceof Replies) {
      queues[key] ??= [...reply.list];
      reply = queues[key].length > 1 ? queues[key].shift() : queues[key][0];
    }
    if (Array.isArray(reply) && typeof reply[0] === "number")
      return Promise.resolve(
        new Response(JSON.stringify(reply[1]), { status: reply[0] }),
      );
    return Promise.resolve(new Response(JSON.stringify(reply)));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const calls = (fetchMock: ReturnType<typeof backend>, key: string) =>
  fetchMock.mock.calls.filter(
    ([path, init]) => `${init?.method ?? "GET"} ${path}` === key,
  );

const box = () => screen.getByLabelText("Ask the Copilot");

function openConversation() {
  localStorage.setItem(
    `undercover-copilot-conversation-${dana.handler_id}`,
    ID,
  );
}

beforeEach(() => {
  signIn();
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("greets the handler by first name and sends a suggested question", async () => {
  const working = { ...turn, state: "working", answer: null } as const;
  const fetchMock = backend({
    "POST /api/conversations": { ...conversation, title: "" },
    [`POST ${PATH}/messages`]: working,
    [`GET ${PATH}`]: detail(working),
  });
  render(<Copilot handler={dana} />);
  expect(screen.getByText("Hello, Dana.")).toBeDefined();
  expect(screen.getByText("Signed in as Dana Whitfield")).toBeDefined();

  fireEvent.click(screen.getByRole("button", { name: "What needs me today?" }));

  expect(await screen.findByText("What needs me today?")).toBeDefined();
  const [, init] = calls(fetchMock, `POST ${PATH}/messages`)[0];
  expect(JSON.parse(init?.body as string)).toEqual({
    message: "What needs me today?",
  });
  expect(init?.headers).toMatchObject({ Authorization: "Bearer token" });
  expect(box()).toHaveProperty("disabled", true);
  expect(screen.getByText("The Copilot is working")).toBeDefined();
});

it("sends with Enter, and Shift+Enter starts a new line", async () => {
  const fetchMock = backend({
    [`GET ${PATH}`]: detail(turn),
    [`POST ${PATH}/messages`]: { ...turn, id: 2, state: "working" },
  });
  openConversation();
  render(<Copilot handler={dana} />);
  await screen.findByText("Nothing needs you.");

  fireEvent.change(box(), { target: { value: "Why CLM-1012?" } });
  fireEvent.keyDown(box(), { key: "Enter", shiftKey: true });
  expect(calls(fetchMock, `POST ${PATH}/messages`)).toHaveLength(0);

  fireEvent.keyDown(box(), { key: "Enter" });
  await waitFor(() => {
    expect(calls(fetchMock, `POST ${PATH}/messages`)).toHaveLength(1);
  });
  // The conversation was open, so no new one was made.
  expect(calls(fetchMock, "POST /api/conversations")).toHaveLength(0);
  await waitFor(() => {
    expect(box()).toHaveProperty("value", "");
  });
});

it("shows each step's card working, done, refused and failed", async () => {
  const startedAt = new Date(Date.now() - 3000).toISOString();
  backend({
    [`GET ${PATH}`]: detail({
      ...turn,
      state: "working",
      answer: null,
      steps: [
        step,
        {
          ...step,
          key: "s2",
          kind: "action",
          tool: "sign_off",
          input: "CLM-1013",
          state: "refused",
          detail: { ...step.detail, answer: "Over your approval limit" },
        } as CopilotStep,
        {
          ...step,
          key: "s3",
          kind: "context",
          tool: "lookup_term",
          input: "baggage",
          state: "failed",
          detail: null,
        },
        {
          ...step,
          key: "s4",
          tool: "get_queue",
          input: "",
          state: "working",
          seconds: null,
          started_at: startedAt,
        },
      ],
    }),
  });
  openConversation();
  render(<Copilot handler={dana} />);

  const done = await screen.findByRole("button", {
    name: /get_claim CLM-1012/,
  });
  expect(done.textContent).toContain("1.2 s");

  const refused = screen.getByRole("button", { name: /sign_off CLM-1013/ });
  expect(within(refused).getByText("Refused")).toBeDefined();
  expect(screen.getByText("Over your approval limit")).toBeDefined();
  expect(refused.closest("li")?.className).toContain("action");

  const failed = screen.getByRole("button", { name: /lookup_term baggage/ });
  expect(within(failed).getByText("Failed")).toBeDefined();
  expect(screen.getByText("No reason was given.")).toBeDefined();

  const working = screen.getByRole("button", { name: /get_queue/ });
  expect(working.textContent).toMatch(/Working, [34] s/);
});

it("opens a card to show what was asked and what came back", async () => {
  backend({
    [`GET ${PATH}`]: detail({
      ...turn,
      steps: [
        {
          ...step,
          kind: "context",
          tool: "lookup_term",
          input: "baggage",
          detail: {
            asked: "lookup_term baggage",
            answer: "Baggage means the bags a traveler takes.",
            file: "glossary.md",
            owner: "Claims operations",
          },
        },
      ],
    }),
  });
  openConversation();
  render(<Copilot handler={dana} />);
  const card = await screen.findByRole("button", { name: /lookup_term/ });
  expect(card.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByText("glossary.md")).toBeNull();

  fireEvent.click(card);
  expect(card.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("lookup_term baggage")).toBeDefined();
  expect(
    screen.getByText("Baggage means the bags a traveler takes."),
  ).toBeDefined();
  expect(screen.getByText("glossary.md")).toBeDefined();
  expect(screen.getByText("Claims operations")).toBeDefined();
});

const waiting: Turn = {
  ...turn,
  state: "waiting_for_confirmation",
  answer: null,
  card: {
    claim_id: "CLM-1013",
    customer_name: "Maya Patel",
    amount: 1400,
    reason: "Covered under section 4.2",
  },
};

it.each([
  ["Sign off and pay", "sign_off"],
  ["Cancel", "cancel"],
])("answers a confirmation with %s", async (button, answer) => {
  const fetchMock = backend({
    [`GET ${PATH}`]: new Replies([
      detail(waiting),
      detail({ ...waiting, state: "working", card: null }),
    ]),
    [`POST ${PATH}/confirmation`]: { ...waiting, state: "working" },
  });
  openConversation();
  render(<Copilot handler={dana} />);
  const card = await screen.findByRole("region", {
    name: "Confirm the sign-off",
  });
  expect(within(card).getByText("Maya Patel")).toBeDefined();
  expect(within(card).getByText("$1,400.00")).toBeDefined();
  expect(within(card).getByText("Covered under section 4.2")).toBeDefined();
  expect(
    within(card).getByRole("link", { name: "CLM-1013" }).getAttribute("href"),
  ).toBe("#/claims/CLM-1013");

  fireEvent.click(within(card).getByRole("button", { name: button }));
  await waitFor(() => {
    expect(calls(fetchMock, `POST ${PATH}/confirmation`)).toHaveLength(1);
  });
  const [, init] = calls(fetchMock, `POST ${PATH}/confirmation`)[0];
  expect(JSON.parse(init?.body as string)).toEqual({ answer });
  await waitFor(() => {
    expect(
      screen.queryByRole("region", { name: "Confirm the sign-off" }),
    ).toBeNull();
  });
});

it("asks the handler to answer a waiting confirmation first", async () => {
  backend({ [`GET ${PATH}`]: detail(waiting) });
  openConversation();
  render(<Copilot handler={dana} />);
  await screen.findByRole("button", { name: "Sign off and pay" });
  expect(box()).toHaveProperty("disabled", true);
  expect(box().getAttribute("placeholder")).toBe(
    "Answer the confirmation above first",
  );
  expect(
    screen.getByText("Answer the confirmation above first."),
  ).toBeDefined();
});

it("collapses to a tab and remembers it in the browser", async () => {
  backend({});
  const { unmount } = render(<Copilot handler={dana} />);
  fireEvent.click(screen.getByRole("button", { name: "Hide the Copilot" }));
  expect(screen.queryByLabelText("Ask the Copilot")).toBeNull();
  expect(localStorage.getItem("undercover-copilot-open")).toBe("closed");
  unmount();

  render(<Copilot handler={dana} />);
  const tab = screen.getByRole("button", { name: "Open the Copilot" });
  fireEvent.click(tab);
  expect(box()).toBeDefined();
  expect(localStorage.getItem("undercover-copilot-open")).toBe("open");
  await waitFor(() => {
    expect(screen.getByText("Hello, Dana.")).toBeDefined();
  });
});

it("opens even when the browser keeps nothing", () => {
  backend({});
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  render(<Copilot handler={dana} />);
  fireEvent.click(screen.getByRole("button", { name: "Hide the Copilot" }));
  expect(
    screen.getByRole("button", { name: "Open the Copilot" }),
  ).toBeDefined();
});

it("reads the conversation about once a second only while a turn works", async () => {
  const working = { ...turn, state: "working", answer: null } as const;
  const fetchMock = backend({
    [`GET ${PATH}`]: new Replies([
      detail(working),
      detail(working),
      detail(turn),
    ]),
  });
  openConversation();
  render(<Copilot handler={dana} />);
  await screen.findByText("Nothing needs you.", undefined, {
    timeout: POLL_MS * 4,
  });
  expect(calls(fetchMock, `GET ${PATH}`)).toHaveLength(3);
  expect(screen.getByText("2 model calls, $0.0042, 6.5 s")).toBeDefined();

  // Done, so no more reads.
  await act(() => new Promise((resolve) => setTimeout(resolve, POLL_MS * 1.5)));
  expect(calls(fetchMock, `GET ${PATH}`)).toHaveLength(3);
}, 10_000);

it("renews the sign-in before a message when less than 20 minutes are left", async () => {
  signIn({ ...session, expires_at: Math.floor(Date.now() / 1000) + 300 });
  const refresh = vi
    .spyOn(supabase().auth, "refreshSession")
    .mockResolvedValue({
      data: { session: { ...session, access_token: "renewed" }, user: null },
      error: null,
    });
  const fetchMock = backend({
    [`GET ${PATH}`]: detail(turn),
    [`POST ${PATH}/messages`]: { ...turn, id: 2, state: "working" },
  });
  openConversation();
  render(<Copilot handler={dana} />);
  await screen.findByText("Nothing needs you.");
  // Reads don't renew it.
  expect(refresh).not.toHaveBeenCalled();

  fireEvent.change(box(), { target: { value: "And now?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => {
    expect(calls(fetchMock, `POST ${PATH}/messages`)).toHaveLength(1);
  });
  expect(refresh).toHaveBeenCalledOnce();
  const [, init] = calls(fetchMock, `POST ${PATH}/messages`)[0];
  expect(init?.headers).toMatchObject({ Authorization: "Bearer renewed" });
});

it("keeps the sign-in when more than 20 minutes are left", async () => {
  signIn({ ...session, expires_at: Math.floor(Date.now() / 1000) + 3000 });
  const refresh = vi.spyOn(supabase().auth, "refreshSession");
  const fetchMock = backend({
    [`GET ${PATH}`]: detail(turn),
    [`POST ${PATH}/messages`]: { ...turn, id: 2, state: "working" },
  });
  openConversation();
  render(<Copilot handler={dana} />);
  await screen.findByText("Nothing needs you.");
  fireEvent.change(box(), { target: { value: "And now?" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => {
    expect(calls(fetchMock, `POST ${PATH}/messages`)).toHaveLength(1);
  });
  expect(refresh).not.toHaveBeenCalled();
});

it("shows a failed turn's error and lets the handler send again", async () => {
  backend({
    [`GET ${PATH}`]: detail({
      ...turn,
      state: "failed",
      answer: null,
      error: "The model didn't answer.",
    }),
  });
  openConversation();
  render(<Copilot handler={dana} />);
  expect(
    await screen.findByText(
      /The model didn't answer\. You can send a new message\./,
    ),
  ).toBeDefined();
  expect(box()).toHaveProperty("disabled", false);
});

it("shows a stopped turn", async () => {
  backend({
    [`GET ${PATH}`]: detail({ ...turn, state: "stopped", answer: null }),
  });
  openConversation();
  render(<Copilot handler={dana} />);
  expect(
    await screen.findByText(/This stopped before it finished/),
  ).toBeDefined();
});

it("says why a message was refused", async () => {
  backend({
    [`GET ${PATH}`]: detail(turn),
    [`POST ${PATH}/messages`]: [409, { detail: "A turn is still working" }],
  });
  openConversation();
  render(<Copilot handler={dana} />);
  await screen.findByText("Nothing needs you.");
  fireEvent.change(box(), { target: { value: "Again" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "A turn is still working",
  );
  expect(box()).toHaveProperty("value", "Again");
});

it("lists earlier conversations newest first and opens one", async () => {
  const older = { ...conversation, id: "older", title: "Older question" };
  const fetchMock = backend({
    "GET /api/conversations": [conversation, older],
    "GET /api/conversations/older": { ...older, turns: [turn] },
  });
  render(<Copilot handler={dana} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Earlier conversations (2)" }),
  );
  const titles = screen
    .getAllByRole("button")
    .map((button) => button.textContent)
    .filter((text) => text.endsWith("AM") || text.endsWith("PM"));
  expect(titles[0]).toMatch(/^What needs me today\?/);
  expect(titles[1]).toMatch(/^Older question/);

  fireEvent.click(screen.getByRole("button", { name: /^Older question/ }));
  expect(await screen.findByText("Nothing needs you.")).toBeDefined();
  expect(calls(fetchMock, "GET /api/conversations/older")).toHaveLength(1);
  expect(
    localStorage.getItem(`undercover-copilot-conversation-${dana.handler_id}`),
  ).toBe("older");

  fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
  expect(screen.getByText("Hello, Dana.")).toBeDefined();
});

it("forgets a saved conversation the app no longer has", async () => {
  backend({});
  openConversation();
  render(<Copilot handler={dana} />);
  await waitFor(() => {
    expect(
      localStorage.getItem(
        `undercover-copilot-conversation-${dana.handler_id}`,
      ),
    ).toBeNull();
  });
  expect(screen.getByText("Hello, Dana.")).toBeDefined();
});

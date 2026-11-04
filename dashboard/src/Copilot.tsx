import { useEffect, useRef, useState } from "react";
import type { Handler } from "./api";
import {
  answerConfirmation,
  createConversation,
  getConversation,
  listConversations,
  sendMessage,
  type ConfirmationAnswer,
  type Conversation,
  type ConversationDetail,
} from "./copilotApi";
import { CloseIcon, CopilotIcon, SendIcon } from "./CopilotIcons";
import {
  saveConversation,
  savedConversation,
  savedOpen,
  saveOpen,
} from "./copilotStorage";
import { TurnView } from "./CopilotTurn";
import { when } from "./format";

// While the Copilot works on a turn, the sidebar reads the conversation about once a second.
export const POLL_MS = 1000;

const SUGGESTIONS = [
  "What needs me today?",
  "Work the queue and tell me what needs me.",
  "Which payouts are held for my sign-off?",
  "Are we meeting the success criteria we agreed?",
];

const reason = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

function Greeting({
  handler,
  onAsk,
}: Readonly<{ handler: Handler; onAsk: (question: string) => void }>) {
  return (
    <div className="copilot-empty">
      <p className="lede">Hello, {handler.name.split(" ")[0]}.</p>
      <p className="muted">
        Ask about claims, customers and policies, have the agent work the queue,
        or check how the pilot is doing.
      </p>
      <ul className="suggestions" aria-label="Suggested questions">
        {SUGGESTIONS.map((question) => (
          <li key={question}>
            <button
              type="button"
              className="suggestion"
              onClick={() => {
                onAsk(question);
              }}
            >
              {question}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function History({
  conversations,
  current,
  onPick,
}: Readonly<{
  conversations: Conversation[];
  current: string | undefined;
  onPick: (id: string) => void;
}>) {
  const [open, setOpen] = useState(false);
  if (conversations.length === 0) return null;
  return (
    <div className="copilot-history">
      <button
        type="button"
        className="quiet"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
        }}
      >
        Earlier conversations ({conversations.length})
      </button>
      {open && (
        <ul className="plain">
          {conversations.map((conversation) => (
            <li key={conversation.id}>
              <button
                type="button"
                className="history-item"
                aria-current={conversation.id === current ? "true" : undefined}
                onClick={() => {
                  setOpen(false);
                  onPick(conversation.id);
                }}
              >
                <span>{conversation.title || "New conversation"}</span>
                <span className="small muted">
                  {when(conversation.created_at)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Composer({
  blocked,
  waiting,
  onSend,
}: Readonly<{
  blocked: boolean;
  waiting: boolean;
  onSend: (message: string) => Promise<boolean>;
}>) {
  const [draft, setDraft] = useState("");
  const send = () => {
    if (draft.trim() === "" || blocked || waiting) return;
    void onSend(draft.trim()).then((sent) => {
      if (sent) setDraft("");
    });
  };
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      <textarea
        aria-label="Ask the Copilot"
        placeholder={
          waiting ? "Answer the confirmation above first" : "Ask the Copilot"
        }
        rows={2}
        value={draft}
        disabled={blocked || waiting}
        onChange={(event) => {
          setDraft(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        }}
      />
      <button
        type="submit"
        aria-label="Send"
        disabled={blocked || waiting || draft.trim() === ""}
      >
        <SendIcon />
      </button>
    </form>
  );
}

// The conversation's state, read from the app, and the turns the handler starts.
function useConversation(handler: Handler) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [current, setCurrent] = useState<ConversationDetail | null>(null);
  const [now, setNow] = useState(0);
  const [sending, setSending] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const id = handler.handler_id;

  const show = (detail: ConversationDetail) => {
    setCurrent(detail);
    setNow(Date.now());
    saveConversation(id, detail.id);
  };

  useEffect(() => {
    listConversations().then(setConversations, () => {
      // Without the list, only the open conversation shows.
    });
    const saved = savedConversation(id);
    if (saved)
      getConversation(saved).then(
        (detail) => {
          setCurrent(detail);
          setNow(Date.now());
        },
        () => {
          saveConversation(id, null);
        },
      );
  }, [id]);

  const latest = current?.turns.at(-1);
  const working = latest?.state === "working";

  useEffect(() => {
    if (!current || !working) return;
    let stopped = false;
    const timer = setTimeout(() => {
      getConversation(current.id).then(
        (detail) => {
          if (stopped) return;
          setCurrent(detail);
          setNow(Date.now());
        },
        () => {
          // Try again on the next tick.
          if (!stopped) setNow(Date.now());
        },
      );
    }, POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [current, working, now]);

  async function act(work: () => Promise<string>) {
    setSending(true);
    setRefused(null);
    try {
      show(await getConversation(await work()));
      return true;
    } catch (error) {
      setRefused(reason(error));
      return false;
    } finally {
      setSending(false);
    }
  }

  const send = (message: string) =>
    act(async () => {
      const conversation = current ?? (await createConversation());
      await sendMessage(conversation.id, message);
      // The app titles a conversation with its first question.
      if (!current)
        setConversations((list) => [
          { ...conversation, title: message },
          ...list,
        ]);
      return conversation.id;
    });

  // A confirmation card shows only in the open conversation.
  const answer = (choice: ConfirmationAnswer) => {
    if (current)
      void act(async () => {
        await answerConfirmation(current.id, choice);
        return current.id;
      });
  };

  const pick = (picked: string) => void act(() => Promise.resolve(picked));

  const startAfresh = () => {
    setCurrent(null);
    setRefused(null);
    saveConversation(id, null);
  };

  return {
    conversations,
    current,
    now,
    sending,
    refused,
    working,
    waiting: latest?.state === "waiting_for_confirmation",
    send,
    answer,
    pick,
    startAfresh,
  };
}

// The UnderCover Copilot, in a sidebar on every signed-in page.
export function Copilot({ handler }: Readonly<{ handler: Handler }>) {
  const [open, setOpen] = useState(savedOpen);
  const chat = useConversation(handler);
  const scroller = useRef<HTMLDivElement>(null);
  const shown = chat.current;

  // Keeps the latest step in view as the conversation grows.
  useEffect(() => {
    const element = scroller.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [shown]);

  const toggle = (next: boolean) => {
    setOpen(next);
    saveOpen(next);
  };

  if (!open)
    return (
      <aside className="copilot collapsed" aria-label="Copilot">
        <button
          type="button"
          className="copilot-tab"
          aria-label="Open the Copilot"
          onClick={() => {
            toggle(true);
          }}
        >
          <CopilotIcon />
          <span aria-hidden="true">Copilot</span>
        </button>
      </aside>
    );

  const turns = chat.current?.turns ?? [];
  const blocked = chat.sending || chat.working;
  return (
    <aside className="copilot" aria-label="Copilot">
      <header className="copilot-head">
        <h2>
          <CopilotIcon /> Copilot
        </h2>
        <button
          type="button"
          className="quiet"
          disabled={blocked}
          onClick={chat.startAfresh}
        >
          New conversation
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label="Hide the Copilot"
          onClick={() => {
            toggle(false);
          }}
        >
          <CloseIcon />
        </button>
      </header>
      <History
        conversations={chat.conversations}
        current={chat.current?.id}
        onPick={chat.pick}
      />
      <div className="copilot-body" ref={scroller}>
        {turns.length === 0 ? (
          <Greeting
            handler={handler}
            onAsk={(question) => {
              if (!blocked) void chat.send(question);
            }}
          />
        ) : (
          <ol className="turns" aria-label="Conversation">
            {turns.map((turn) => (
              <TurnView
                key={turn.id}
                turn={turn}
                now={chat.now}
                busy={chat.sending}
                onAnswer={chat.answer}
              />
            ))}
          </ol>
        )}
      </div>
      <footer className="copilot-foot">
        {chat.refused && (
          <p className="refusal" role="alert">
            {chat.refused}
          </p>
        )}
        {chat.waiting && (
          <p className="small muted">Answer the confirmation above first.</p>
        )}
        <Composer blocked={blocked} waiting={chat.waiting} onSend={chat.send} />
        <p className="small muted">Signed in as {handler.name}</p>
      </footer>
    </aside>
  );
}

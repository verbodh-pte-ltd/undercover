import type { ConfirmationAnswer, ConfirmationCard, Turn } from "./copilotApi";
import { cost, money } from "./format";
import { Markdown } from "./Markdown";
import { StepCard } from "./StepCard";

// Before the Copilot signs off and pays, the handler confirms here. The app fills the card from
// its records.
function Confirmation({
  card,
  busy,
  onAnswer,
}: Readonly<{
  card: ConfirmationCard;
  busy: boolean;
  onAnswer: (answer: ConfirmationAnswer) => void;
}>) {
  return (
    <section className="confirm-card" aria-label="Confirm the sign-off">
      <h3>Sign off and pay this claim?</h3>
      <dl className="facts">
        <dt>Claim</dt>
        <dd>
          <a href={`#/claims/${card.claim_id}`}>{card.claim_id}</a>
        </dd>
        <dt>Customer</dt>
        <dd>{card.customer_name}</dd>
        <dt>Pays</dt>
        <dd className="mono">{money(card.amount)}</dd>
        <dt>Agent's reason</dt>
        <dd>{card.reason}</dd>
      </dl>
      <div className="actions">
        <button
          type="button"
          className="secondary"
          disabled={busy}
          onClick={() => {
            onAnswer("cancel");
          }}
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            onAnswer("sign_off");
          }}
        >
          Sign off and pay
        </button>
      </div>
    </section>
  );
}

function smallPrint(turn: Turn) {
  const calls = `${String(turn.model_calls)} model ${turn.model_calls === 1 ? "call" : "calls"}`;
  const time = `${(turn.seconds ?? 0).toFixed(1)} s`;
  return `${calls}, ${cost(turn.cost)}, ${time}`;
}

function Ending({ turn }: Readonly<{ turn: Turn }>) {
  if (turn.state === "failed")
    return (
      <p className="turn-error">
        {turn.error ?? "The Copilot couldn't finish this."} You can send a new
        message.
      </p>
    );
  if (turn.state === "stopped")
    return (
      <p className="turn-error">
        This stopped before it finished. You can send a new message.
      </p>
    );
  return null;
}

export function TurnView({
  turn,
  now,
  busy,
  onAnswer,
}: Readonly<{
  turn: Turn;
  now: number;
  busy: boolean;
  onAnswer: (answer: ConfirmationAnswer) => void;
}>) {
  return (
    <li className="turn">
      <p className="bubble mine">{turn.message}</p>
      {turn.steps.length > 0 && (
        <ol className="step-cards" aria-label="Steps">
          {turn.steps.map((step) => (
            <StepCard key={step.key} step={step} now={now} />
          ))}
        </ol>
      )}
      {turn.state === "working" && (
        <p className="thinking">
          <span aria-hidden="true" />
          <span aria-hidden="true" />
          <span aria-hidden="true" />
          <span className="hidden">The Copilot is working</span>
        </p>
      )}
      {turn.state === "waiting_for_confirmation" && turn.card && (
        <Confirmation card={turn.card} busy={busy} onAnswer={onAnswer} />
      )}
      {turn.answer && (
        <div className="bubble theirs">
          <Markdown text={turn.answer} />
        </div>
      )}
      {turn.state === "done" && (
        <p className="small-print">{smallPrint(turn)}</p>
      )}
      <Ending turn={turn} />
    </li>
  );
}

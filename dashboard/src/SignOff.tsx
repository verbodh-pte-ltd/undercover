import { useState } from "react";
import { sendBack, signOff, type Claim, type Handler } from "./api";
import { money } from "./format";

export const NOTE_LIMIT = 1000;

export function SignOff({
  claim,
  handler,
  onAnswered,
}: Readonly<{
  claim: Claim;
  handler: Handler;
  onAnswered: (claim: Claim) => void;
}>) {
  const [refused, setRefused] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const why = claim.held_by_gateway
    ? "The Gateway refused the agent's payment, so this payout waits for a handler's sign-off."
    : "This payout is over the approval limit, so it waits for a handler's sign-off.";
  const refuse = (error: unknown) => {
    setRefused(error instanceof Error ? error.message : String(error));
  };

  if (!handler.can_approve_over_limit) {
    return (
      <p className="note">
        {why} You can't sign off payouts over the approval limit, so ask a
        handler who can to sign it off or send it back.
      </p>
    );
  }
  return (
    <div className="stack-sm">
      <form
        className="sign-off"
        onSubmit={(event) => {
          event.preventDefault();
          signOff(claim.claim_id).then(onAnswered, refuse);
        }}
      >
        <p>{why} Signing it off pays it.</p>
        <button type="submit">Sign off {money(claim.payout ?? 0)}</button>
      </form>
      <form
        className="stack-sm"
        onSubmit={(event) => {
          event.preventDefault();
          sendBack(claim.claim_id, note.trim()).then(onAnswered, refuse);
        }}
      >
        <label htmlFor="send-back-note">
          Or send it back to the agent with a note saying what to look at again.
          The agent decides the claim again.
        </label>
        <textarea
          id="send-back-note"
          rows={3}
          required
          maxLength={NOTE_LIMIT}
          value={note}
          onChange={(event) => {
            setNote(event.target.value);
          }}
        />
        <div className="actions">
          <span className="muted">
            {note.length.toLocaleString("en-US")} of{" "}
            {NOTE_LIMIT.toLocaleString("en-US")} characters
          </span>
          <button
            type="submit"
            className="secondary"
            disabled={note.trim() === ""}
          >
            Send back
          </button>
        </div>
      </form>
      {refused && (
        <p className="refusal" role="alert">
          {refused}
        </p>
      )}
    </div>
  );
}

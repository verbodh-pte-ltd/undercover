import { useState } from "react";
import { signOff, type Claim, type Handler } from "./api";
import { money } from "./format";

export function SignOff({
  claim,
  handler,
  onSigned,
}: Readonly<{
  claim: Claim;
  handler: Handler;
  onSigned: (claim: Claim) => void;
}>) {
  const [refused, setRefused] = useState<string | null>(null);
  const why = claim.held_by_gateway
    ? "The Gateway refused the agent's payment, so this payout waits for a handler's sign-off."
    : "This payout is over the approval limit, so it waits for a handler's sign-off.";

  if (!handler.can_approve_over_limit) {
    return (
      <p className="note">
        {why} You can't sign off payouts over the approval limit, so ask a
        handler who can to sign it off.
      </p>
    );
  }
  return (
    <form
      className="sign-off"
      onSubmit={(event) => {
        event.preventDefault();
        signOff(claim.claim_id).then(onSigned, (error: unknown) => {
          setRefused(error instanceof Error ? error.message : String(error));
        });
      }}
    >
      <p>{why} Signing it off pays it.</p>
      <button type="submit">Sign off {money(claim.payout ?? 0)}</button>
      {refused && (
        <p className="refusal" role="alert">
          {refused}
        </p>
      )}
    </form>
  );
}

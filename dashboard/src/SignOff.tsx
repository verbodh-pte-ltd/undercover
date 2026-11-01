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

  if (!handler.can_approve_over_limit) {
    return (
      <p className="note">
        This payout is over the approval limit, so it waits for a handler's
        sign-off. You can't sign off payouts over the approval limit, so ask a
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
      <p>
        This payout is over the approval limit, so it waits for a handler's
        sign-off. The agent pays it once you sign off.
      </p>
      <button type="submit">Sign off {money(claim.payout ?? 0)}</button>
      {refused && (
        <p className="refusal" role="alert">
          {refused}
        </p>
      )}
    </form>
  );
}

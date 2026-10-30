import { useEffect, useState } from "react";
import { listHandlers, signOff, type Claim, type Handler } from "./api";
import { money } from "./format";

export function SignOff({
  claim,
  onSigned,
}: Readonly<{ claim: Claim; onSigned: (claim: Claim) => void }>) {
  const [handlers, setHandlers] = useState<Handler[]>([]);
  const [handlerId, setHandlerId] = useState("");

  useEffect(() => {
    void listHandlers().then((loaded) => {
      setHandlers(loaded);
      setHandlerId(loaded.at(0)?.handler_id ?? "");
    });
  }, []);

  return (
    <form
      className="sign-off"
      onSubmit={(event) => {
        event.preventDefault();
        void signOff(claim.claim_id, handlerId).then(onSigned);
      }}
    >
      <p>
        This payout is over $1,000, so it waits for a handler's sign-off. The
        agent pays it once a handler signs off.
      </p>
      <label htmlFor="handler">Handler</label>
      <select
        id="handler"
        value={handlerId}
        onChange={(event) => {
          setHandlerId(event.target.value);
        }}
      >
        {handlers.map((handler) => (
          <option key={handler.handler_id} value={handler.handler_id}>
            {handler.name}
          </option>
        ))}
      </select>
      <button type="submit" disabled={!handlerId}>
        Sign off {money(claim.payout ?? 0)}
      </button>
    </form>
  );
}

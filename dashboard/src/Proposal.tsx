import { useState } from "react";
import {
  approveProposal,
  rejectProposal,
  type Proposal,
  type Round,
} from "./api";
import { Confirm } from "./Confirm";
import { Diff } from "./Diff";

// A proposal waiting for a handler: the diff, the model and effort before and after,
// and the reason, with Approve and Reject for a handler who can act on it.
export function ProposalPanel({
  round,
  proposal,
  canAct,
  onAnswered,
}: Readonly<{
  round: Round;
  proposal: Proposal;
  canAct: boolean;
  onAnswered: () => void;
}>) {
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const change = proposal.kind === "change";
  const answered = (refusal: string | null) => {
    setRefused(refusal);
    if (refusal === null) onAnswered();
  };
  return (
    <div className="panel stack-sm">
      <h2>
        {change
          ? "A change to the claim agent waits for approval"
          : "A new recipe for the improver waits for approval"}
      </h2>
      <dl className="facts">
        {change && (
          <>
            <dt>Model</dt>
            <dd className="mono">
              {proposal.before.model} to {proposal.model}
            </dd>
            <dt>Reasoning effort</dt>
            <dd className="mono">
              {proposal.before.effort} to {proposal.effort}
            </dd>
          </>
        )}
        <dt>Reason</dt>
        <dd>{proposal.reason}</dd>
      </dl>
      <h3>
        {change ? "Changes to the instructions" : "Changes to the recipe"}
      </h3>
      <Diff lines={proposal.changed_lines} />
      {canAct && (
        <form
          className="stack-sm"
          onSubmit={(event) => {
            event.preventDefault();
            void rejectProposal(round.round_id, reason).then(answered);
          }}
        >
          <label htmlFor="reject-reason">
            Your reason, if you reject it. It goes back to the improver.
          </label>
          <textarea
            id="reject-reason"
            rows={2}
            required
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
          <div className="actions">
            {refused && (
              <p className="refusal" role="alert">
                {refused}
              </p>
            )}
            <button type="submit" className="secondary">
              Reject
            </button>
            <button
              type="button"
              onClick={() => {
                setAsking(true);
              }}
            >
              Approve
            </button>
          </div>
        </form>
      )}
      {asking && (
        <Confirm
          title={change ? "Approve this change?" : "Approve this recipe?"}
          action="Approve"
          onConfirm={() => {
            setAsking(false);
            void approveProposal(round.round_id).then(answered);
          }}
          onCancel={() => {
            setAsking(false);
          }}
        >
          <p>
            {change
              ? "It becomes a new version under test: evaluated once on the evaluation set and twice on the held-out set, then kept or rolled back by the rule."
              : "It becomes the recipe the next round follows."}
          </p>
        </Confirm>
      )}
    </div>
  );
}

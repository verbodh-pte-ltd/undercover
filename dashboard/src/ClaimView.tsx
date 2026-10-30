import { useEffect, useState } from "react";
import {
  getClaim,
  getClaimHistory,
  getPolicy,
  listHandlers,
  listSteps,
  type Claim,
  type Handler,
  type PastClaim,
  type Policy,
  type Step,
} from "./api";
import { label, money, sectionLabel, when } from "./format";
import { StatusChip } from "./Queue";
import { SignOff } from "./SignOff";

function Payment({
  claim,
  handlers,
}: Readonly<{ claim: Claim; handlers: Handler[] }>) {
  const handler = handlers.find((h) => h.handler_id === claim.signed_off_by);
  return (
    <>
      {claim.signed_off_by && claim.signed_off_at && (
        <p className="muted">
          Signed off by {handler?.name ?? claim.signed_off_by} on{" "}
          {when(claim.signed_off_at)}
        </p>
      )}
      <p className="muted">
        {claim.paid_amount === null
          ? "Not paid yet"
          : `Paid ${money(claim.paid_amount)}`}
        {claim.paid_at && ` on ${when(claim.paid_at)}`}
      </p>
    </>
  );
}

function Decision({
  claim,
  handlers,
}: Readonly<{ claim: Claim; handlers: Handler[] }>) {
  if (!claim.decision) {
    return <p className="muted">The agent has not decided this claim yet.</p>;
  }
  return (
    <div className="stack-sm">
      <p className="decision">
        {label(claim.decision)}
        {claim.decision === "approve" && claim.payout !== null && (
          <span className="mono"> {money(claim.payout)}</span>
        )}
      </p>
      <p>{claim.reason}</p>
      <p className="muted">Policy section: {claim.policy_section}</p>
      {claim.decision === "approve" && (
        <Payment claim={claim} handlers={handlers} />
      )}
    </div>
  );
}

function Steps({ steps }: Readonly<{ steps: Step[] }>) {
  if (steps.length === 0) {
    return <p className="muted">No steps yet.</p>;
  }
  return (
    <ol className="steps">
      {steps.map((step) => (
        <li key={step.id} className={step.kind}>
          <span className="step-kind">{label(step.kind)}</span>
          <span className="step-text">{step.text}</span>
        </li>
      ))}
    </ol>
  );
}

function PolicyPanel({ policy }: Readonly<{ policy: Policy }>) {
  return (
    <dl className="facts">
      <dt>Policy</dt>
      <dd className="mono">{policy.policy_id}</dd>
      <dt>Plan</dt>
      <dd>{label(policy.plan)}</dd>
      <dt>Add-ons</dt>
      <dd>
        {policy.add_ons.length ? policy.add_ons.map(label).join(", ") : "None"}
      </dd>
      <dt>Trip</dt>
      <dd>
        {policy.trip.destination}, {policy.trip.departs_on} to{" "}
        {policy.trip.returns_on}
      </dd>
      <dt>Trip cost</dt>
      <dd className="mono">{money(policy.trip.trip_cost)}</dd>
      <dt>Travelers</dt>
      <dd>{policy.travelers.join(", ")}</dd>
    </dl>
  );
}

function History({ history }: Readonly<{ history: PastClaim[] }>) {
  if (history.length === 0) return <p className="muted">No earlier claims.</p>;
  return (
    <ul className="stack-sm plain">
      {history.map((past) => (
        <li key={past.claim_id}>
          <span className="mono">{past.claim_id}</span>, {past.filed_on}:{" "}
          {past.summary}. {label(past.outcome)}, paid{" "}
          <span className="mono">{money(past.amount_paid)}</span>
        </li>
      ))}
    </ul>
  );
}

export const POLL_MS = 2000;

const loadClaim = (claimId: string) =>
  Promise.all([getClaim(claimId), listSteps(claimId)]);

export function ClaimView({ claimId }: Readonly<{ claimId: string }>) {
  const [claim, setClaim] = useState<Claim | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [history, setHistory] = useState<PastClaim[]>([]);
  const [handlers, setHandlers] = useState<Handler[]>([]);

  useEffect(() => {
    void loadClaim(claimId).then(([loaded, loadedSteps]) => {
      setClaim(loaded);
      setSteps(loadedSteps);
      void getPolicy(loaded.policy_id).then(setPolicy);
      void getClaimHistory(loaded.customer_id).then(setHistory);
    });
    void listHandlers().then(setHandlers);
  }, [claimId]);

  // Until the agent has decided, keep checking for its new steps.
  const undecided =
    claim?.status === "waiting" || claim?.status === "in_progress";
  useEffect(() => {
    if (!undecided) return;
    const timer = setInterval(() => {
      void loadClaim(claimId).then(([loaded, loadedSteps]) => {
        setClaim(loaded);
        setSteps(loadedSteps);
      });
    }, POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, [claimId, undecided]);

  if (!claim) return <p className="muted">Loading {claimId}</p>;

  return (
    <section className="stack">
      <a href="#/">Back to the queue</a>
      <header className="claim-head">
        <h1 className="mono">{claim.claim_id}</h1>
        <StatusChip status={claim.status} />
      </header>
      <p className="lede">{claim.summary}</p>
      <div className="columns">
        <div className="stack">
          <div className="panel stack-sm">
            <h2>Decision</h2>
            <Decision claim={claim} handlers={handlers} />
            {claim.status === "waiting_for_sign_off" && (
              <SignOff claim={claim} onSigned={setClaim} />
            )}
          </div>
          <div className="panel stack-sm">
            <h2>Story</h2>
            <p className="muted">
              {claim.customer_name}, {label(claim.claim_type)},{" "}
              {claim.incident_location}, {claim.incident_date}
            </p>
            <p>{claim.story}</p>
          </div>
          <div className="panel stack-sm">
            <h2>Items claimed</h2>
            <table>
              <tbody>
                {claim.items.map((item) => (
                  <tr key={item.description}>
                    <td>{item.description}</td>
                    <td className="num mono">{money(item.amount)}</td>
                  </tr>
                ))}
                <tr className="total">
                  <td>Total claimed</td>
                  <td className="num mono">{money(claim.amount_claimed)}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="panel stack-sm">
            <h2>Documents</h2>
            {claim.documents.map((doc) => (
              <div key={doc.name} className="doc">
                <h3>{doc.name}</h3>
                <p>{doc.content}</p>
              </div>
            ))}
          </div>
        </div>
        <div className="stack">
          <div className="panel stack-sm">
            <h2>What the agent did</h2>
            <Steps steps={steps} />
          </div>
          <div className="panel stack-sm">
            <h2>Sections the agent read</h2>
            {claim.sections_read.length === 0 ? (
              <p className="muted">None yet.</p>
            ) : (
              <ul className="stack-sm plain">
                {claim.sections_read.map((section) => (
                  <li key={section}>{sectionLabel(section)}</li>
                ))}
              </ul>
            )}
          </div>
          <div className="panel stack-sm">
            <h2>Policy</h2>
            {policy && <PolicyPanel policy={policy} />}
          </div>
          <div className="panel stack-sm">
            <h2>Earlier claims</h2>
            <History history={history} />
          </div>
        </div>
      </div>
    </section>
  );
}

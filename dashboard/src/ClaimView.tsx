import { useEffect, useState } from "react";
import {
  getClaim,
  getClaimHistory,
  getPolicy,
  listHandlers,
  listSteps,
  type Claim,
  type Handler,
  type Calculation,
  type PastClaim,
  type Policy,
  type Source,
  type Step,
  type Teammate,
} from "./api";
import { day, label, money, when } from "./format";
import { LoadFailed, PageHeader } from "./Page";
import { StatusChip } from "./Queue";
import { SignOff } from "./SignOff";

function Payment({
  claim,
  handlers,
}: Readonly<{ claim: Claim; handlers: Teammate[] }>) {
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

function CalculationTable({
  calculation,
}: Readonly<{ calculation: Calculation }>) {
  return (
    <table>
      <tbody>
        {calculation.lines.map((line, index) => (
          <tr key={`${String(index)}: ${line.step}`}>
            <td>{line.step}</td>
            <td className="num mono">{money(line.amount)}</td>
          </tr>
        ))}
        <tr className="total">
          <td>Payout</td>
          <td className="num mono">{money(calculation.payout)}</td>
        </tr>
      </tbody>
    </table>
  );
}

function Decision({
  claim,
  handlers,
}: Readonly<{ claim: Claim; handlers: Teammate[] }>) {
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
      {claim.calculation && (
        <CalculationTable calculation={claim.calculation} />
      )}
      {claim.decided_by && claim.decided_at && (
        <p className="muted">
          Decided by {claim.decided_by} on {when(claim.decided_at)}
        </p>
      )}
      {claim.manual_version && (
        <p className="muted">Claims manual version {claim.manual_version}</p>
      )}
      {claim.decision === "approve" && (
        <Payment claim={claim} handlers={handlers} />
      )}
    </div>
  );
}

const stepLabel = (step: Step) => {
  if (step.kind === "refusal") {
    return step.detail?.by === "gateway" ? "Refused by the Gateway" : "Refused";
  }
  return step.kind === "memory_save" ? "Memory saved" : label(step.kind);
};

// Long answers fold to one line until they are opened.
const folded = new Set(["tool_result", "memory_search"]);

function Steps({ steps }: Readonly<{ steps: Step[] }>) {
  if (steps.length === 0) {
    return <p className="muted">No steps yet.</p>;
  }
  return (
    <ol className="steps">
      {steps.map((step) => (
        <li key={step.id} className={step.kind}>
          <span className="step-kind">{stepLabel(step)}</span>
          {folded.has(step.kind) ? (
            // A tool's result can be long JSON: one line until it is opened.
            <details>
              <summary className="step-text">{step.text}</summary>
            </details>
          ) : (
            <span className="step-text">{step.text}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

interface Used {
  source: Source;
  lookups: string[];
}

// Everything looked up in the context layer, once each, grouped by file.
function contextUsed(steps: Step[]): Used[] {
  const byFile = new Map<string, Used>();
  for (const step of steps) {
    if (step.kind !== "lookup") continue;
    for (const source of step.detail?.files ?? []) {
      const used = byFile.get(source.file) ?? { source, lookups: [] };
      const what = step.detail?.looked_up ?? step.text;
      if (!used.lookups.includes(what)) used.lookups.push(what);
      byFile.set(source.file, used);
    }
  }
  return [...byFile.values()];
}

function ContextUsed({ steps }: Readonly<{ steps: Step[] }>) {
  const used = contextUsed(steps);
  if (used.length === 0) return <p className="muted">Nothing yet.</p>;
  return (
    <ul className="stack plain">
      {used.map(({ source, lookups }) => (
        <li key={source.file} className="stack-sm">
          <p>
            <span className="mono">{source.file}</span>
            <br />
            <span className="muted">
              {source.system}, owned by {source.owner}, reviewed{" "}
              {day(source.reviewed)}
            </span>
          </p>
          <ul>
            {lookups.map((what) => (
              <li key={what}>{what}</li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
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
        {policy.trip.destination}, {day(policy.trip.departs_on)} to{" "}
        {day(policy.trip.returns_on)}
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
          <span className="mono">{past.claim_id}</span>, {day(past.filed_on)}:{" "}
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

export function ClaimView({
  claimId,
  handler,
}: Readonly<{ claimId: string; handler: Handler }>) {
  const [claim, setClaim] = useState<Claim | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [history, setHistory] = useState<PastClaim[]>([]);
  const [handlers, setHandlers] = useState<Teammate[]>([]);

  const [failed, setFailed] = useState(false);

  useEffect(() => {
    loadClaim(claimId).then(
      ([loaded, loadedSteps]) => {
        setClaim(loaded);
        setSteps(loadedSteps);
        void getPolicy(loaded.policy_id).then(setPolicy);
        void getClaimHistory(loaded.customer_id).then(setHistory);
      },
      () => {
        setFailed(true);
      },
    );
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

  if (!claim)
    return (
      <section className="stack">
        <a href="#/">Back to the queue</a>
        <PageHeader title={<span className="mono">{claimId}</span>}>
          One claim: the agent's decision, what it did and why.
        </PageHeader>
        {failed ? (
          <LoadFailed what={`Claim ${claimId}`} />
        ) : (
          <p className="muted">Loading {claimId}</p>
        )}
      </section>
    );

  return (
    <section className="stack">
      <a href="#/">Back to the queue</a>
      <header className="page-head">
        <div className="claim-head">
          <h1 className="mono">{claim.claim_id}</h1>
          <StatusChip status={claim.status} />
        </div>
        <p className="lede">{claim.summary}</p>
      </header>
      <div className="columns">
        <div className="stack">
          <div className="panel stack-sm">
            <h2>Decision</h2>
            <Decision claim={claim} handlers={handlers} />
            {claim.run_id !== null && (
              <p className="muted">
                A copy of {claim.copied_from} for evaluation {claim.run_id}.
                Nobody signs off a copy.
              </p>
            )}
            {claim.status === "waiting_for_sign_off" &&
              claim.run_id === null && (
                <SignOff claim={claim} handler={handler} onSigned={setClaim} />
              )}
          </div>
          <div className="panel stack-sm">
            <h2>Story</h2>
            <p className="muted">
              {claim.customer_name}, {label(claim.claim_type)},{" "}
              {claim.incident_location}, {day(claim.incident_date)}
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
            <h2>Context used</h2>
            <ContextUsed steps={steps} />
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

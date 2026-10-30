import { useEffect, useState } from "react";
import {
  getCurrentRun,
  listClaims,
  startDispatch,
  type ClaimSummary,
  type Run,
  type Status,
} from "./api";
import { cost, label, money, seconds, statusLabels } from "./format";

const QUEUE_POLL_MS = 5000;
const AGENT_CHOICES = Array.from({ length: 25 }, (_, i) => i + 1);

export function StatusChip({ status }: Readonly<{ status: Status }>) {
  return <span className={`chip ${status}`}>{statusLabels[status]}</span>;
}

export function AgentPicker({
  n,
  onChange,
}: Readonly<{ n: number; onChange: (n: number) => void }>) {
  return (
    <>
      <label htmlFor="agents">Claim agents at once</label>
      <select
        id="agents"
        value={n}
        onChange={(event) => {
          onChange(Number(event.target.value));
        }}
      >
        {AGENT_CHOICES.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    </>
  );
}

function DispatchForm({ onStarted }: Readonly<{ onStarted: () => void }>) {
  const [n, setN] = useState(3);
  const [refusal, setRefusal] = useState<string | null>(null);
  return (
    <form
      className="panel start-form"
      onSubmit={(event) => {
        event.preventDefault();
        void startDispatch(n).then((reason) => {
          setRefusal(reason);
          if (reason === null) onStarted();
        });
      }}
    >
      <AgentPicker n={n} onChange={setN} />
      <button type="submit">Start dispatch</button>
      {refusal && (
        <p className="refusal" role="alert">
          {refusal}
        </p>
      )}
    </form>
  );
}

function Summary({ run }: Readonly<{ run: Run }>) {
  const { summary } = run;
  return (
    <ul className="plain stack-sm">
      <li>
        {summary.approved} approved, {summary.declined} declined,{" "}
        {summary.referred} referred
      </li>
      {summary.waiting_for_sign_off.map((held) => (
        <li key={held.claim_id}>
          <a className="mono" href={`#/claims/${held.claim_id}`}>
            {held.claim_id}
          </a>{" "}
          waits for sign-off on {money(held.payout)}
        </li>
      ))}
      {summary.left_for_handler.map((claimId) => (
        <li key={claimId}>
          <a className="mono" href={`#/claims/${claimId}`}>
            {claimId}
          </a>{" "}
          is left for a handler
        </li>
      ))}
      {summary.in_progress.map((claimId) => (
        <li key={claimId}>
          <a className="mono" href={`#/claims/${claimId}`}>
            {claimId}
          </a>{" "}
          is still in progress
        </li>
      ))}
    </ul>
  );
}

const runStates = {
  running: "Running",
  finished: "Finished",
  stopped: "Stopped",
};

function RunPanel({ run }: Readonly<{ run: Run }>) {
  return (
    <div className="panel stack-sm">
      <h2>Dispatch {run.run_id}</h2>
      <p className="muted">
        {runStates[run.status]}, {run.n} claim agents at once, {run.model}.{" "}
        {run.claims.length} claims, model cost {cost(run.cost)}, agent time{" "}
        {seconds(run.seconds)}
        {run.run_seconds !== null && `, run time ${seconds(run.run_seconds)}`}
      </p>
      {run.status !== "running" && <Summary run={run} />}
      {run.claims.length === 0 ? (
        <p className="muted">No claims started yet.</p>
      ) : (
        <ol className="plain stack-sm">
          {run.claims.map((worked) => (
            <li key={worked.claim_id}>
              <StatusChip status={worked.status} />{" "}
              <a className="mono" href={`#/claims/${worked.claim_id}`}>
                {worked.claim_id}
              </a>{" "}
              {worked.decision && label(worked.decision)}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

export function Queue() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null);
  const [run, setRun] = useState<Run | null>(null);

  const load = () => {
    void listClaims().then(setClaims);
    void getCurrentRun().then(setRun);
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, QUEUE_POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, []);

  if (!claims) return <p className="muted">Loading the queue</p>;

  const statuses = Object.keys(statusLabels) as Status[];
  return (
    <section className="stack">
      <h1>Claims queue</h1>
      <ul className="counts">
        {statuses.map((status) => (
          <li key={status} className="panel">
            <span className="count">
              {claims.filter((c) => c.status === status).length}
            </span>
            <StatusChip status={status} />
          </li>
        ))}
      </ul>
      <DispatchForm onStarted={load} />
      {run && <RunPanel run={run} />}
      <div className="panel table-wrap">
        <table>
          <thead>
            <tr>
              <th>Claim</th>
              <th>Customer</th>
              <th>Type</th>
              <th className="num">Claimed</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.claim_id}>
                <td>
                  <a className="mono" href={`#/claims/${claim.claim_id}`}>
                    {claim.claim_id}
                  </a>
                  <div className="muted small">{claim.summary}</div>
                </td>
                <td>{claim.customer_name}</td>
                <td>{label(claim.claim_type)}</td>
                <td className="num mono">{money(claim.amount_claimed)}</td>
                <td>
                  <StatusChip status={claim.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

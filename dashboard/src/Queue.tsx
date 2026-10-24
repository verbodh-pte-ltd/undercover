import { useEffect, useState } from "react";
import {
  getCurrentRun,
  listClaims,
  type ClaimSummary,
  type Run,
  type Status,
} from "./api";
import { label, money, statusLabels, todoLabels } from "./format";

const QUEUE_POLL_MS = 5000;

export function StatusChip({ status }: Readonly<{ status: Status }>) {
  return <span className={`chip ${status}`}>{statusLabels[status]}</span>;
}

function RunPanel({ run }: Readonly<{ run: Run }>) {
  return (
    <div className="panel stack-sm">
      <h2>Current run</h2>
      <p className="muted">
        {run.claims_started.length} of {run.cap} claims started
        {run.summary ? ", finished" : ""}
      </p>
      {run.todos.length === 0 ? (
        <p className="muted">The agent has not planned this run yet.</p>
      ) : (
        <ol className="todos plain stack-sm">
          {run.todos.map((todo) => (
            <li key={todo.claim_id}>
              <span className={`chip ${todo.state}`}>
                {todoLabels[todo.state]}
              </span>{" "}
              <a className="mono" href={`#/claims/${todo.claim_id}`}>
                {todo.claim_id}
              </a>{" "}
              {todo.text}
            </li>
          ))}
        </ol>
      )}
      {run.summary && <p>{run.summary}</p>}
    </div>
  );
}

export function Queue() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null);
  const [run, setRun] = useState<Run | null>(null);

  useEffect(() => {
    const load = () => {
      void listClaims().then(setClaims);
      void getCurrentRun().then(setRun);
    };
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

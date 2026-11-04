import { useState } from "react";
import type { CopilotStep, CopilotStepDetail } from "./copilotApi";
import { KindIcon } from "./CopilotIcons";

const KIND_LABELS: Record<CopilotStep["kind"], string> = {
  claims: "Claims",
  customers: "Customers and policies",
  context: "Context layer",
  work: "The agent's work",
  memory: "Memory",
  action: "Action",
};

const secondsText = (value: number) => `${value.toFixed(1)} s`;

function StepState({
  step,
  now,
}: Readonly<{ step: CopilotStep; now: number }>) {
  if (step.state === "working") {
    const soFar = Math.max(0, (now - Date.parse(step.started_at)) / 1000);
    return (
      <span className="step-state">
        <span className="spinner" aria-hidden="true" />
        <span className="hidden">Working, </span>
        {String(Math.floor(soFar))} s
      </span>
    );
  }
  if (step.state === "done")
    return <span className="step-state">{secondsText(step.seconds ?? 0)}</span>;
  return (
    <span className={`chip ${step.state}`}>
      {step.state === "refused" ? "Refused" : "Failed"}
    </span>
  );
}

function DetailView({
  detail,
}: Readonly<{ detail: CopilotStepDetail | null }>) {
  const rows = [
    ["Asked", detail?.asked],
    ["Answer", detail?.answer],
    ["File", detail?.file],
    ["Owner", detail?.owner],
  ].filter((row): row is [string, string] => typeof row[1] === "string");
  if (rows.length === 0)
    return <p className="step-detail muted">Nothing more to show.</p>;
  return (
    <dl className="step-detail">
      {rows.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

// One tool the Copilot called: its kind, name, main input and state, opening to show what was
// asked and what came back.
export function StepCard({
  step,
  now,
}: Readonly<{ step: CopilotStep; now: number }>) {
  const [open, setOpen] = useState(false);
  const stopped = step.state === "refused" || step.state === "failed";
  return (
    <li className={`step-card ${step.kind} ${step.state}`}>
      <button
        type="button"
        className="step-head"
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
        }}
      >
        <span className="step-icon" title={KIND_LABELS[step.kind]}>
          <KindIcon kind={step.kind} />
          <span className="hidden">{KIND_LABELS[step.kind]}: </span>
        </span>
        <span className="step-call">
          <span className="step-tool">{step.tool}</span>{" "}
          <span className="step-input">{step.input}</span>
        </span>
        <StepState step={step} now={now} />
      </button>
      {stopped && (
        <p className="step-reason">
          {step.detail?.answer ?? "No reason was given."}
        </p>
      )}
      {open && <DetailView detail={step.detail} />}
    </li>
  );
}

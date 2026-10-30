import { useEffect, useState } from "react";
import {
  getEvaluation,
  listEvaluations,
  startEvaluation,
  type EvalSet,
  type Evaluation,
  type EvaluationSummary,
} from "./api";
import { cost, label, money, percent, seconds } from "./format";
import { AgentPicker } from "./Queue";

const SCOREBOARD_POLL_MS = 5000;

const setLabels: Record<EvalSet, string> = {
  evaluation: "Evaluation set",
  held_out: "Held-out set",
};

function EvaluationForm({ onStarted }: Readonly<{ onStarted: () => void }>) {
  const [n, setN] = useState(3);
  const [evalSet, setEvalSet] = useState<EvalSet>("evaluation");
  const [tiny, setTiny] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  return (
    <form
      className="panel start-form"
      onSubmit={(event) => {
        event.preventDefault();
        void startEvaluation(n, evalSet, tiny).then((reason) => {
          setRefusal(reason);
          if (reason === null) onStarted();
        });
      }}
    >
      <label htmlFor="eval-set">Set</label>
      <select
        id="eval-set"
        value={evalSet}
        onChange={(event) => {
          setEvalSet(event.target.value as EvalSet);
        }}
      >
        {Object.entries(setLabels).map(([value, text]) => (
          <option key={value} value={value}>
            {text}
          </option>
        ))}
      </select>
      <label className="inline">
        <input
          type="checkbox"
          checked={tiny}
          onChange={(event) => {
            setTiny(event.target.checked);
          }}
        />{" "}
        Only its five tiny claims
      </label>
      <AgentPicker n={n} onChange={setN} />
      <button type="submit">Start evaluation</button>
      {refusal && (
        <p className="refusal" role="alert">
          {refusal}
        </p>
      )}
    </form>
  );
}

const runStates = {
  running: "Running",
  finished: "Finished",
  stopped: "Stopped",
};

export function Scoreboard() {
  const [evaluations, setEvaluations] = useState<EvaluationSummary[] | null>(
    null,
  );

  const load = () => {
    void listEvaluations().then(setEvaluations);
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, SCOREBOARD_POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, []);

  return (
    <section className="stack">
      <h1>Scoreboard</h1>
      <p className="muted">
        How the agent's decisions compare with our handlers' on labelled claims.
      </p>
      <EvaluationForm onStarted={load} />
      {evaluations === null && <p className="muted">Loading evaluations</p>}
      {evaluations?.length === 0 && (
        <p className="muted">No evaluations yet.</p>
      )}
      {evaluations && evaluations.length > 0 && (
        <div className="panel table-wrap">
          <table>
            <thead>
              <tr>
                <th>Evaluation</th>
                <th>Set</th>
                <th className="num">Agents</th>
                <th>Model</th>
                <th className="num">Decisions right</th>
                <th className="num">Right</th>
                <th className="num">Cost of mistakes</th>
                <th className="num">Straight through</th>
                <th className="num">Model cost per claim</th>
                <th className="num">Time per claim</th>
                <th className="num">Run time</th>
              </tr>
            </thead>
            <tbody>
              {evaluations.map((e) => (
                <tr key={e.run_id}>
                  <td>
                    <a href={`#/scoreboard/${String(e.run_id)}`}>
                      Evaluation {e.run_id}
                    </a>
                    <div className="muted small">{runStates[e.status]}</div>
                  </td>
                  <td>
                    {setLabels[e.eval_set]}
                    {e.tiny && <div className="muted small">Tiny</div>}
                  </td>
                  <td className="num mono">{e.n}</td>
                  <td>
                    {e.model}
                    <div className="muted small">Effort {e.effort}</div>
                  </td>
                  <td className="num mono">{percent(e.decision_accuracy)}</td>
                  <td className="num mono">{percent(e.accuracy)}</td>
                  <td className="num mono">{money(e.cost_of_mistakes)}</td>
                  <td className="num mono">{percent(e.straight_through)}</td>
                  <td className="num mono">{cost(e.cost_per_claim)}</td>
                  <td className="num mono">{seconds(e.seconds_per_claim)}</td>
                  <td className="num mono">{seconds(e.run_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function EvaluationView({ runId }: Readonly<{ runId: string }>) {
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);

  useEffect(() => {
    void getEvaluation(runId).then(setEvaluation);
  }, [runId]);

  if (!evaluation) return <p className="muted">Loading evaluation {runId}</p>;

  const misses = evaluation.claims.filter((c) => !c.right).length;
  return (
    <section className="stack">
      <a href="#/scoreboard">Back to the scoreboard</a>
      <h1>Evaluation {evaluation.run_id}</h1>
      <p className="muted">
        {setLabels[evaluation.eval_set]}
        {evaluation.tiny ? ", tiny" : ""}, {evaluation.n} claim agents at once,{" "}
        {evaluation.model}. {runStates[evaluation.status]}.{" "}
        {percent(evaluation.accuracy)} right, {misses} missed, cost of mistakes{" "}
        {money(evaluation.cost_of_mistakes)}.
      </p>
      <div className="panel table-wrap">
        <table>
          <thead>
            <tr>
              <th>Claim</th>
              <th>Handler</th>
              <th>Agent</th>
              <th className="num">Cost of the mistake</th>
              <th className="num">Model cost</th>
              <th className="num">Time</th>
            </tr>
          </thead>
          <tbody>
            {evaluation.claims.map((c) => (
              <tr key={c.claim_id} className={c.right ? "" : "miss"}>
                <td>
                  <a className="mono" href={`#/claims/${c.claim_id}`}>
                    {c.claim_id}
                  </a>
                  <div className="muted small">
                    {c.right ? "Right" : "Miss"}
                  </div>
                </td>
                <td>
                  {label(c.handler)}{" "}
                  <span className="mono">{money(c.handler_payout)}</span>
                </td>
                <td>
                  {label(c.agent)}{" "}
                  <span className="mono">{money(c.agent_payout)}</span>
                </td>
                <td className="num mono">{money(c.mistake_cost)}</td>
                <td className="num mono">{cost(c.cost)}</td>
                <td className="num mono">{seconds(c.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

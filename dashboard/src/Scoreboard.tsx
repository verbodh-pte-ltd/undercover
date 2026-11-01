import { useEffect, useState } from "react";
import {
  getEvaluation,
  listEvaluations,
  startEvaluation,
  type EvalSet,
  type Evaluation,
  type EvaluationClaim,
  type EvaluationSummary,
} from "./api";
import { cost, grade, label, money, percent, seconds, yesNo } from "./format";
import { LoadFailed, PageHeader } from "./Page";
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

  const [failed, setFailed] = useState(false);

  const load = () => {
    listEvaluations().then(
      (loaded) => {
        setEvaluations(loaded);
        setFailed(false);
      },
      () => {
        setFailed(true);
      },
    );
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
      <PageHeader title="Scoreboard">
        How the agent's decisions compare with our handlers' on labelled claims.
      </PageHeader>
      <EvaluationForm onStarted={load} />
      {failed && <LoadFailed what="The evaluations" />}
      {evaluations === null && !failed && (
        <p className="muted">Loading evaluations</p>
      )}
      {evaluations?.length === 0 && (
        <div className="panel stack-sm">
          <p>No evaluations yet.</p>
          <p className="muted">
            Start one above to score the agent against our handlers' decisions.
          </p>
        </div>
      )}
      {evaluations && evaluations.length > 0 && (
        <div className="panel table-wrap">
          <table>
            <thead>
              <tr>
                <th>Evaluation</th>
                <th>Set</th>
                <th className="num">Version</th>
                <th>Model</th>
                <th className="num">Decision right</th>
                <th className="num">Decision and payout right</th>
                <th className="num">Cost of mistakes</th>
                <th className="num">Straight through</th>
                <th className="num">Grade</th>
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
                    <div>
                      <span className={`chip ${e.status}`}>
                        {runStates[e.status]}
                      </span>
                    </div>
                  </td>
                  <td>
                    {setLabels[e.eval_set]}
                    {e.tiny && <div className="muted small">Tiny</div>}
                  </td>
                  <td className="num mono">{e.version}</td>
                  <td>
                    <span className="nowrap">{e.model}</span>
                    <div className="muted small">
                      Effort {e.effort}, {e.n} agents at once
                    </div>
                  </td>
                  <td className="num mono">{percent(e.decision_accuracy)}</td>
                  <td className="num mono">{percent(e.accuracy)}</td>
                  <td className="num mono">{money(e.cost_of_mistakes)}</td>
                  <td className="num mono">{percent(e.straight_through)}</td>
                  <td className="num mono">{grade(e.grade)}</td>
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

function JudgeCells({ c }: Readonly<{ c: EvaluationClaim }>) {
  if (c.grade === null || c.same_grounds === null || c.same_section === null)
    return (
      <td colSpan={3} className="num muted">
        Not graded
      </td>
    );
  return (
    <>
      <td className="num mono">
        <strong>{c.letter}</strong>, {grade(c.grade)}
      </td>
      <td>{yesNo(c.same_grounds)}</td>
      <td>{yesNo(c.same_section)}</td>
    </>
  );
}

export function EvaluationView({ runId }: Readonly<{ runId: string }>) {
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    getEvaluation(runId).then(setEvaluation, () => {
      setFailed(true);
    });
  }, [runId]);

  const header = (
    <>
      <a href="#/scoreboard">Back to the scoreboard</a>
      <PageHeader title={`Evaluation ${runId}`}>
        One evaluation's claims, the agent's decisions beside our handlers'.
      </PageHeader>
    </>
  );
  if (!evaluation)
    return (
      <section className="stack">
        {header}
        {failed ? (
          <LoadFailed what={`Evaluation ${runId}`} />
        ) : (
          <p className="muted">Loading evaluation {runId}</p>
        )}
      </section>
    );

  const misses = evaluation.claims.filter((c) => !c.right).length;
  return (
    <section className="stack">
      {header}
      <p className="muted">
        {setLabels[evaluation.eval_set]}
        {evaluation.tiny ? ", tiny" : ""}, {evaluation.n} claim agents at once,{" "}
        {evaluation.model}, version {evaluation.version}.{" "}
        {runStates[evaluation.status]}. {percent(evaluation.accuracy)} right,{" "}
        {misses} missed, cost of mistakes {money(evaluation.cost_of_mistakes)}.
        Grade {grade(evaluation.grade)}, judge's cost{" "}
        {cost(evaluation.judge_cost)}.
      </p>
      <div className="panel table-wrap">
        <table>
          <thead>
            <tr>
              <th>Claim</th>
              <th>Handler</th>
              <th>Agent</th>
              <th className="num">Cost of the mistake</th>
              <th className="num">Grade</th>
              <th>Same grounds</th>
              <th>Same section</th>
              <th className="num">Model cost</th>
              <th className="num">Time</th>
            </tr>
          </thead>
          <tbody>
            {evaluation.claims.map((c) => (
              <tr key={c.claim_id} className={c.right ? "" : "miss"}>
                <td>
                  <a className="mono" href={`#/claims/${c.claim_id}`}>
                    {c.copied_from}
                  </a>
                  <div>
                    <span className={`chip ${c.right ? "right" : "missed"}`}>
                      {c.right ? "Right" : "Miss"}
                    </span>
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
                <JudgeCells c={c} />
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

import { useEffect, useState } from "react";
import {
  listRecipes,
  listRounds,
  listVersions,
  startRound,
  stopRound,
  type ConfigVersion,
  type Handler,
  type Recipe,
  type Round,
  type VersionScores,
} from "./api";
import { VersionCharts } from "./Chart";
import { Confirm } from "./Confirm";
import { Diff } from "./Diff";
import { cost, grade, percent, when } from "./format";
import { useHandlerNames } from "./handlers";
import { LoadFailed, PageHeader } from "./Page";
import { ProposalPanel } from "./Proposal";

const ROUND_POLL_MS = 3000;

const STAGES = [
  "First evaluations",
  "Improver working",
  "Change waiting for approval",
  "Evaluations",
  "Verdict",
  "Recipe waiting for approval",
  "Finished",
];

const closed = (r: Round) => r.stage === "finished" || r.stage === "stopped";

// Where a round has got to in STAGES. The improver works three times: before its
// proposal, between an approval and the evaluations, and after the verdict.
function stepOf(r: Round) {
  switch (r.stage) {
    case "first_evaluations":
      return 0;
    case "improving":
      if (r.version === null) return 1;
      return r.kept === null ? 3 : 5;
    case "change_waiting":
      return 2;
    case "evaluations":
      return 3;
    case "verdict":
      return 4;
    case "recipe_waiting":
      return 5;
    default:
      return 6;
  }
}

function roundStatus(r: Round) {
  if (r.stage === "stopped") return { name: "stopped", text: "Stopped" };
  if (r.stage === "finished") return { name: "finished", text: "Finished" };
  if (r.proposal) return { name: "approval", text: "Waiting for approval" };
  return { name: "working", text: "Working" };
}

function Stages({ round }: Readonly<{ round: Round }>) {
  const at = stepOf(round);
  const { progress } = round;
  return (
    <ol
      className="stages"
      aria-label={`Round ${String(round.round_id)} stages`}
    >
      {STAGES.map((text, i) => {
        const state = i < at || round.stage === "finished" ? "done" : "to_do";
        const now = i === at && round.stage !== "finished";
        return (
          <li
            key={text}
            className={now ? "now" : state}
            aria-current={now ? "step" : undefined}
          >
            <span className="stage-dot" aria-hidden="true">
              {i < at || round.stage === "finished" ? "✓" : i + 1}
            </span>
            <span>
              {text}
              {now && progress && progress.needed > 0 && (
                <span className="stage-detail mono">
                  {Math.min(Math.max(progress.started, 1), progress.needed)} of{" "}
                  {progress.needed}
                </span>
              )}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function stopped(round: Round, name: (id: string) => string) {
  const by = round.stopped_by ? `by ${name(round.stopped_by)}` : "by a restart";
  const at = round.finished_at ? ` on ${when(round.finished_at)}` : "";
  return ` Stopped ${by}${at}.`;
}

function RoundDetails({
  round,
  name,
}: Readonly<{ round: Round; name: (id: string) => string }>) {
  return (
    <>
      <p className="muted">
        Started by {name(round.started_by)} on {when(round.started_at)},
        following recipe version {round.recipe_version}. Spent{" "}
        <span className="mono">{cost(round.spent)}</span> of its{" "}
        <span className="mono">{cost(round.spent + round.left)}</span> limit.
        {round.stage === "stopped" && stopped(round, name)}
      </p>
      {round.stage !== "stopped" && <Stages round={round} />}
      {round.kept !== null && (
        <p>
          Version {round.version}{" "}
          <span className={`chip ${round.kept ? "kept" : "rolled_back"}`}>
            {round.kept ? "Kept" : "Rolled back"}
          </span>{" "}
          {round.why && `because ${round.why}.`}
        </p>
      )}
      {round.events.length > 0 && (
        <ul className="plain small stack-sm">
          {round.events.map((e) => (
            <li key={e.decided_at}>
              {e.handler} {e.decision === "approve" ? "approved" : "rejected"}{" "}
              the {e.kind} on {when(e.decided_at)}
              {e.reason && `: ${e.reason}`}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function RoundPanel({
  round,
  canAct,
  name,
  onChanged,
}: Readonly<{
  round: Round | undefined;
  canAct: boolean;
  name: (id: string) => string;
  onChanged: () => void;
}>) {
  const [asking, setAsking] = useState<"start" | "stop" | null>(null);
  const [refused, setRefused] = useState<string | null>(null);
  const open = round && !closed(round);
  const act = (action: Promise<string | null>) => {
    setAsking(null);
    void action.then((refusal) => {
      setRefused(refusal);
      onChanged();
    });
  };
  const status = round && roundStatus(round);
  return (
    <div className="panel stack-sm">
      <div className="panel-head">
        <h2>{round ? `Round ${String(round.round_id)}` : "No rounds yet"}</h2>
        {status && <span className={`chip ${status.name}`}>{status.text}</span>}
        {canAct && (
          <button
            type="button"
            className={open ? "danger" : ""}
            onClick={() => {
              setAsking(open ? "stop" : "start");
            }}
          >
            {open ? "Stop the round" : "Start a round"}
          </button>
        )}
      </div>
      {round ? (
        <RoundDetails round={round} name={name} />
      ) : (
        <p className="muted">
          A round runs the improver once: it proposes a better version of the
          claim agent, which is evaluated and kept or rolled back, then a better
          recipe.
        </p>
      )}
      {refused && (
        <p className="refusal" role="alert">
          {refused}
        </p>
      )}
      {asking === "start" && (
        <Confirm
          title="Start a round?"
          action="Start a round"
          onConfirm={() => {
            act(startRound());
          }}
          onCancel={() => {
            setAsking(null);
          }}
        >
          <p>
            The improver works to the settings as they stand now and spends no
            more than the round's spending limit. Nothing it proposes takes
            effect until a handler approves it.
          </p>
        </Confirm>
      )}
      {asking === "stop" && round && (
        <Confirm
          title={`Stop round ${String(round.round_id)}?`}
          action="Stop the round"
          onConfirm={() => {
            act(stopRound(round.round_id));
          }}
          onCancel={() => {
            setAsking(null);
          }}
        >
          <p>
            A version not yet judged is rolled back, and the current version
            stays as it is.
          </p>
        </Confirm>
      )}
    </div>
  );
}

const shown = (s: VersionScores | null, show: (s: VersionScores) => string) =>
  s ? show(s) : "None";

const scores = (
  before: VersionScores | null,
  after: VersionScores | null,
  show: (s: VersionScores) => string,
) =>
  before || after ? `${shown(before, show)} to ${shown(after, show)}` : "None";

function Rounds({ rounds }: Readonly<{ rounds: Round[] }>) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Round</th>
            <th>Status</th>
            <th>Version tried</th>
            <th className="num">Held-out accuracy</th>
            <th className="num">Grade</th>
            <th>Verdict</th>
            <th className="num">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rounds.map((r) => {
            const status = roundStatus(r);
            return (
              <tr key={r.round_id}>
                <td>
                  <span className="mono">{r.round_id}</span>
                  <div className="muted small">{when(r.started_at)}</div>
                </td>
                <td>
                  <span className={`chip ${status.name}`}>{status.text}</span>
                </td>
                <td>
                  {r.version === null ? (
                    <span className="muted">None</span>
                  ) : (
                    <>
                      Version {r.version}
                      <div className="muted small">{r.reason}</div>
                    </>
                  )}
                </td>
                <td className="num mono">
                  {scores(r.held_out_before, r.held_out_after, (s) =>
                    percent(s.accuracy),
                  )}
                </td>
                <td className="num mono">
                  {scores(r.held_out_before, r.held_out_after, (s) =>
                    grade(s.grade).replace(" of 3", ""),
                  )}
                </td>
                <td>
                  {r.kept === null ? (
                    <span className="muted">None</span>
                  ) : (
                    <>
                      <span
                        className={`chip ${r.kept ? "kept" : "rolled_back"}`}
                      >
                        {r.kept ? "Kept" : "Rolled back"}
                      </span>
                      <div className="muted small">{r.why}</div>
                    </>
                  )}
                </td>
                <td className="num mono">{cost(r.spent)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Recipes({
  recipes,
  name,
}: Readonly<{ recipes: Recipe[]; name: (id: string) => string }>) {
  const [shown, setShown] = useState<number | null>(null);
  const opened = recipes.find((r) => r.version === shown);
  return (
    <>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>Reason</th>
              <th>Proposed by</th>
              <th>Approved</th>
              <th>
                <span className="hidden">Changes</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {recipes.map((r) => (
              <tr key={r.version}>
                <td className="mono">{r.version}</td>
                <td>{r.reason}</td>
                <td>{name(r.proposed_by)}</td>
                <td>
                  {name(r.approved_by)}
                  <div className="muted small">{when(r.approved_at)}</div>
                </td>
                <td>
                  {r.changed_lines && (
                    <button
                      type="button"
                      className="quiet"
                      aria-pressed={shown === r.version}
                      onClick={() => {
                        setShown(shown === r.version ? null : r.version);
                      }}
                    >
                      {shown === r.version
                        ? "Hide changes"
                        : `Changes in recipe ${String(r.version)}`}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {opened?.changed_lines && (
        <>
          <h3>
            Recipe {opened.version} against recipe {opened.version - 1}
          </h3>
          <Diff lines={opened.changed_lines} />
        </>
      )}
    </>
  );
}

interface Loaded {
  rounds: Round[];
  versions: ConfigVersion[];
  recipes: Recipe[];
}

export function Improvement({ handler }: Readonly<{ handler: Handler }>) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);
  const name = useHandlerNames();

  const load = () => {
    Promise.all([listRounds(), listVersions(), listRecipes()]).then(
      ([rounds, versions, recipes]) => {
        setLoaded({ rounds, versions, recipes });
        setFailed(false);
      },
      () => {
        setFailed(true);
      },
    );
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, ROUND_POLL_MS);
    return () => {
      clearInterval(timer);
    };
  }, []);

  const canAct = handler.can_approve_over_limit;
  const latest = loaded?.rounds[0];
  return (
    <section className="stack">
      <PageHeader title="Improvement">
        The improver proposes better versions of the claim agent, a handler
        approves or rejects each one, and the rule keeps or rolls back each
        version on the held-out claims.
      </PageHeader>
      {!canAct && (
        <p className="note">
          You can follow the rounds but not start or stop one, or approve or
          reject a proposal. Only a handler who can sign off payouts over the
          approval limit can.
        </p>
      )}
      {failed && <LoadFailed what="The rounds" />}
      {!loaded && !failed && <p className="muted">Loading the rounds</p>}
      {loaded && (
        <>
          <RoundPanel
            round={latest}
            canAct={canAct}
            name={name}
            onChanged={load}
          />
          {latest?.proposal && !closed(latest) && (
            <ProposalPanel
              key={`${String(latest.round_id)}-${String(latest.events.length)}`}
              round={latest}
              proposal={latest.proposal}
              canAct={canAct}
              onAnswered={load}
            />
          )}
          <div className="panel stack-sm">
            <h2>Versions tried</h2>
            <VersionCharts versions={loaded.versions} />
          </div>
          <div className="panel stack-sm">
            <h2>Rounds so far</h2>
            {loaded.rounds.length === 0 ? (
              <p className="muted">
                No rounds yet.{" "}
                {canAct
                  ? "Start one above to have the improver propose a better version."
                  : "They show here once a handler starts one."}
              </p>
            ) : (
              <Rounds rounds={loaded.rounds} />
            )}
          </div>
          <div className="panel stack-sm">
            <h2>The improver's recipe</h2>
            <p className="muted small">
              The steps the improver follows in a round. Each round ends by
              proposing a better recipe.
            </p>
            <Recipes recipes={loaded.recipes} name={name} />
          </div>
        </>
      )}
    </section>
  );
}

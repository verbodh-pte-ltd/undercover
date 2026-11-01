import { Fragment, useEffect, useState } from "react";
import { listVersions, type ConfigVersion } from "./api";
import { Diff } from "./Diff";
import { cost, grade, percent, seconds, when } from "./format";
import { useHandlerNames } from "./handlers";
import { LoadFailed, PageHeader } from "./Page";

function status(v: ConfigVersion) {
  if (v.current) return { name: "current", text: "Current" };
  if (v.kept === null) return { name: "under_test", text: "Under test" };
  return v.kept
    ? { name: "kept", text: "Kept" }
    : { name: "rolled_back", text: "Rolled back" };
}

function Scores({ v }: Readonly<{ v: ConfigVersion }>) {
  if (!v.held_out)
    return (
      <td colSpan={4} className="num muted">
        Not scored yet
      </td>
    );
  return (
    <>
      <td className="num mono">{percent(v.held_out.accuracy)}</td>
      <td className="num mono">{grade(v.held_out.grade)}</td>
      <td className="num mono">{cost(v.held_out.cost_per_claim)}</td>
      <td className="num mono">{seconds(v.held_out.seconds_per_claim)}</td>
    </>
  );
}

function Changes({ v }: Readonly<{ v: ConfigVersion }>) {
  return (
    <div className="panel stack-sm">
      <h2>
        Version {v.version} against version {v.version - 1}
      </h2>
      {v.before && (
        <dl className="facts">
          <dt>Model</dt>
          <dd className="mono">
            {v.before.model} to {v.model}
          </dd>
          <dt>Reasoning effort</dt>
          <dd className="mono">
            {v.before.effort} to {v.effort}
          </dd>
          <dt>Reason</dt>
          <dd>{v.reason}</dd>
        </dl>
      )}
      <Diff lines={v.changed_lines ?? []} />
    </div>
  );
}

export function Configuration() {
  const [versions, setVersions] = useState<ConfigVersion[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [shown, setShown] = useState<number | null>(null);
  const name = useHandlerNames();

  useEffect(() => {
    listVersions().then(setVersions, () => {
      setFailed(true);
    });
  }, []);

  const header = (
    <PageHeader title="Configuration">
      The claim agent's instructions, model and reasoning effort, kept in
      versions. Every dispatch and evaluation uses the current version and
      records it.
    </PageHeader>
  );
  if (failed)
    return (
      <section className="stack">
        {header}
        <LoadFailed what="The versions" />
      </section>
    );
  if (!versions)
    return (
      <section className="stack">
        {header}
        <p className="muted">Loading the versions</p>
      </section>
    );

  const current = versions.find((v) => v.current);
  const opened = versions.find((v) => v.version === shown);
  return (
    <section className="stack">
      {header}
      {current && (
        <div className="panel stack-sm">
          <h2>Current version: version {current.version}</h2>
          <dl className="facts">
            <dt>Model</dt>
            <dd className="mono">{current.model}</dd>
            <dt>Reasoning effort</dt>
            <dd className="mono">{current.effort}</dd>
            <dt>Approved</dt>
            <dd>
              {name(current.approved_by)}, {when(current.approved_at)}
            </dd>
          </dl>
          <h3>Instructions</h3>
          <pre className="instructions">{current.instructions}</pre>
        </div>
      )}
      <div className="panel stack-sm">
        <h2>Versions</h2>
        <p className="muted small">
          Held-out scores are the averages of the whole held-out evaluations
          that improvement rounds ran on a version.
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Model and effort</th>
                <th>Proposed by</th>
                <th>Approved</th>
                <th className="num">Held-out accuracy</th>
                <th className="num">Grade</th>
                <th className="num">Cost per claim</th>
                <th className="num">Time per claim</th>
                <th>Changes</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => {
                const s = status(v);
                return (
                  <Fragment key={v.version}>
                    <tr>
                      <td className="mono">{v.version}</td>
                      <td>
                        <span className={`chip ${s.name}`}>{s.text}</span>
                      </td>
                      <td className="nowrap">
                        {v.model}
                        <div className="muted small">Effort {v.effort}</div>
                      </td>
                      <td>{name(v.proposed_by)}</td>
                      <td>
                        {name(v.approved_by)}
                        <br />
                        <span className="muted small nowrap">
                          {when(v.approved_at)}
                        </span>
                      </td>
                      <Scores v={v} />
                      <td>
                        {v.before && (
                          <button
                            type="button"
                            className="quiet"
                            aria-pressed={shown === v.version}
                            onClick={() => {
                              setShown(shown === v.version ? null : v.version);
                            }}
                          >
                            {shown === v.version
                              ? "Hide changes"
                              : `Changes in version ${String(v.version)}`}
                          </button>
                        )}
                      </td>
                    </tr>
                    {/* The reason reads as a sentence on its own line, under its version. */}
                    <tr className="reason-row">
                      <td />
                      <td colSpan={9}>{v.reason}</td>
                    </tr>
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      {opened && <Changes v={opened} />}
    </section>
  );
}

import { useEffect, useState } from "react";
import {
  changeSettings,
  getSettings,
  type AllowedModel,
  type Handler,
  type SettingValues,
  type Settings as Loaded,
} from "./api";
import { Confirm } from "./Confirm";
import { when } from "./format";
import { LoadFailed, PageHeader } from "./Page";
import { applyTheme, savedTheme, type Theme } from "./theme";

// A setting on the page, by its name or "name.part"; a scale of 100 shows a share as a percent.
interface Field {
  key: string;
  label: string;
  unit: string;
  scale: number;
  max?: number;
}

const MEASURES = [
  {
    name: "accuracy",
    label: "Held-out accuracy",
    unit: "% points",
    scale: 100,
    max: 100,
  },
  { name: "grade", label: "Judge's grade", unit: "points", scale: 1, max: 3 },
  { name: "cost_per_claim", label: "Cost per claim", unit: "%", scale: 100 },
  { name: "seconds_per_claim", label: "Time per claim", unit: "%", scale: 100 },
];

const LIMIT: Field = {
  key: "approval_limit",
  label: "Approval limit",
  unit: "$",
  scale: 1,
};
const OBJECTIVES: Field[] = [
  {
    key: "objectives.accuracy",
    label: "Held-out accuracy at least",
    unit: "%",
    scale: 100,
    max: 100,
  },
  {
    key: "objectives.grade",
    label: "Judge's grade at least",
    unit: "of 3",
    scale: 1,
    max: 3,
  },
];
const margins = (setting: "Noise" | "Tolerance"): Field[] =>
  MEASURES.map((m) => ({
    ...m,
    key: `${setting.toLowerCase()}.${m.name}`,
    label: `${setting} on ${m.label.toLowerCase()}`,
  }));
const NOISE = margins("Noise");
const TOLERANCE = margins("Tolerance");
const ROUNDS: Field[] = [
  {
    key: "round_spending_limit",
    label: "Spending limit for each round",
    unit: "$",
    scale: 1,
  },
  {
    key: "agents_at_once",
    label: "Claim agents at once",
    unit: "agents",
    scale: 1,
    max: 25,
  },
];
const FIELDS = [LIMIT, ...OBJECTIVES, ...NOISE, ...TOLERANCE, ...ROUNDS];

type Values = Record<string, number | Record<string, number>>;

function read(values: SettingValues, key: string) {
  const [name, part] = key.split(".");
  const value = (values as unknown as Values)[name];
  return typeof value === "number" ? value : value[part];
}

// Shows 0.15 as 15 for a percent, without the float's noise.
const shown = (values: SettingValues, f: Field) =>
  String(Number((read(values, f.key) * f.scale).toFixed(6)));

function withDraft(values: SettingValues, draft: Record<string, string>) {
  const next = structuredClone(values) as unknown as Values;
  for (const f of FIELDS) {
    if (!(f.key in draft)) continue;
    const number = Number(draft[f.key]) / f.scale;
    const [name, part] = f.key.split(".");
    const value = next[name];
    if (typeof value === "number") next[name] = number;
    else value[part] = number;
  }
  return next as unknown as SettingValues;
}

const withUnit = (f: Field, text: string) =>
  f.unit === "$" ? `$${text}` : `${text} ${f.unit}`;

const THEMES: { value: Theme; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "Same as the computer" },
];

function Appearance() {
  const [theme, setTheme] = useState(savedTheme);
  return (
    <fieldset className="panel stack-sm">
      <legend>
        <h2>Appearance</h2>
      </legend>
      <p className="muted small">Kept in this browser only.</p>
      <div className="choices">
        {THEMES.map((t) => (
          <label key={t.value} className="inline">
            <input
              type="radio"
              name="theme"
              value={t.value}
              checked={theme === t.value}
              onChange={() => {
                applyTheme(t.value);
                setTheme(t.value);
              }}
            />{" "}
            {t.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Changed({
  loaded,
  names,
}: Readonly<{ loaded: Loaded; names: (keyof SettingValues)[] }>) {
  // The latest change to any of the panel's settings; ISO times sort as text.
  const change = names
    .map((name) => loaded.changes[name])
    .filter((c) => c !== undefined)
    .sort((a, b) => b.changed_at.localeCompare(a.changed_at))
    .at(0);
  return (
    <p className="muted small">
      {change
        ? `Last changed by ${change.handler} on ${when(change.changed_at)}`
        : "Not changed since the start"}
    </p>
  );
}

function Input({
  f,
  values,
  draft,
  readOnly,
  onChange,
}: Readonly<{
  f: Field;
  values: SettingValues;
  draft: Record<string, string>;
  readOnly: boolean;
  onChange: (key: string, text: string) => void;
}>) {
  return (
    <span className="amount">
      <span className="muted">{f.unit === "$" && "$"}</span>
      <input
        id={f.key}
        type="number"
        className="mono num"
        required
        min={0}
        max={f.max}
        step={f.key === "agents_at_once" ? 1 : "any"}
        disabled={readOnly}
        value={draft[f.key] ?? shown(values, f)}
        onChange={(event) => {
          onChange(f.key, event.target.value);
        }}
      />
      <span className="muted">{f.unit !== "$" && f.unit}</span>
    </span>
  );
}

function Changes({
  changed,
  before,
  after,
}: Readonly<{
  changed: Field[];
  before: SettingValues;
  after: SettingValues;
}>) {
  return (
    <ul className="plain">
      {changed.map((f) => (
        <li key={f.key}>
          {f.label}:{" "}
          <span className="mono">
            {withUnit(f, shown(before, f))} to {withUnit(f, shown(after, f))}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ModelsAllowed({ models }: Readonly<{ models: AllowedModel[] }>) {
  return (
    <div className="panel stack-sm">
      <h2>Models allowed</h2>
      <p className="muted small">
        The models the claim agent may use, set in data/models.json. Prices are
        in dollars per million tokens.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th>Reasoning efforts</th>
              <th className="num">Input price</th>
              <th className="num">Output price</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.model}>
                <td className="mono">{m.model}</td>
                <td>{m.reasoning_efforts.join(", ")}</td>
                <td className="num mono">${m.input_price.toFixed(2)}</td>
                <td className="num mono">${m.output_price.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function Settings({ handler }: Readonly<{ handler: Handler }>) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [asking, setAsking] = useState(false);
  const [saved, setSaved] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);

  useEffect(() => {
    getSettings().then(setLoaded, () => {
      setFailed(true);
    });
  }, []);

  const readOnly = !handler.can_approve_over_limit;
  const header = (
    <PageHeader title="Settings">
      The platform's settings: the approval limit, what the improver works to,
      and the models the claim agent may use.
    </PageHeader>
  );
  if (failed)
    return (
      <section className="stack">
        {header}
        <LoadFailed what="The settings" />
      </section>
    );
  if (!loaded)
    return (
      <section className="stack">
        {header}
        <p className="muted">Loading the settings</p>
      </section>
    );

  const values = loaded.settings;
  const next = withDraft(values, draft);
  const changed = FIELDS.filter(
    (f) => read(next, f.key) !== read(values, f.key),
  );
  const edit = (key: string, text: string) => {
    setDraft({ ...draft, [key]: text });
    setSaved(false);
  };
  const input = (f: Field) => (
    <Input
      f={f}
      values={values}
      draft={draft}
      readOnly={readOnly}
      onChange={edit}
    />
  );
  const row = (f: Field) => (
    <div className="setting" key={f.key}>
      <label htmlFor={f.key}>{f.label}</label>
      {input(f)}
    </div>
  );
  const save = () => {
    setAsking(false);
    changeSettings(next).then(
      (result) => {
        setLoaded(result);
        setDraft({});
        setSaved(true);
        setRefused(null);
      },
      (error: unknown) => {
        setRefused(error instanceof Error ? error.message : String(error));
      },
    );
  };

  return (
    <section className="stack">
      {header}
      {readOnly && (
        <p className="note">
          You can see the settings but not change them. Only a handler who can
          sign off payouts over the approval limit can change a setting.
        </p>
      )}
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          setAsking(true);
        }}
      >
        <div className="panel stack-sm">
          <h2>Payouts</h2>
          <Changed loaded={loaded} names={["approval_limit"]} />
          {row(LIMIT)}
          <p className="muted small">
            A payout over the limit waits for a handler's sign-off. A new limit
            applies to the decisions recorded after it; a payout already waiting
            keeps waiting.
          </p>
        </div>
        <div className="panel stack-sm">
          <h2>Business objectives</h2>
          <Changed loaded={loaded} names={["objectives"]} />
          {OBJECTIVES.map(row)}
          <p className="muted small">
            The floors a new version must meet on the held-out claims to be
            kept. The grade runs from 0 (F) to 3 (A).
          </p>
        </div>
        <div className="panel stack-sm">
          <h2>Noise and tolerance</h2>
          <Changed loaded={loaded} names={["noise", "tolerance"]} />
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Measure</th>
                  <th className="num">Noise</th>
                  <th className="num">Tolerance</th>
                </tr>
              </thead>
              <tbody>
                {MEASURES.map((m, i) => (
                  <tr key={m.name}>
                    <td>{m.label}</td>
                    {[NOISE[i], TOLERANCE[i]].map((f) => (
                      <td key={f.key} className="num">
                        <label htmlFor={f.key} className="hidden">
                          {f.label}
                        </label>
                        {input(f)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            A version counts as better when a measure improves by more than its
            noise, and is rolled back when one gets worse by more than its
            tolerance. Accuracy and the grade compare as differences; cost and
            time as a share of the old value.
          </p>
        </div>
        <div className="panel stack-sm">
          <h2>Improvement rounds</h2>
          <Changed
            loaded={loaded}
            names={["round_spending_limit", "agents_at_once"]}
          />
          {ROUNDS.map(row)}
          <p className="muted small">
            The spending limit covers the improver, its evaluations and the
            judge. A round keeps the settings it started with.
          </p>
        </div>
        {!readOnly && (
          <div className="actions">
            {saved && (
              <p className="muted" role="status">
                Saved. The new values apply from now on.
              </p>
            )}
            {refused && (
              <p className="refusal" role="alert">
                {refused}
              </p>
            )}
            <button type="submit" disabled={changed.length === 0}>
              Save changes
            </button>
          </div>
        )}
      </form>
      {asking && (
        <Confirm
          title="Save these changes?"
          action="Save changes"
          onConfirm={save}
          onCancel={() => {
            setAsking(false);
          }}
        >
          <Changes changed={changed} before={values} after={next} />
        </Confirm>
      )}
      <ModelsAllowed models={loaded.models_allowed} />
      <Appearance />
    </section>
  );
}

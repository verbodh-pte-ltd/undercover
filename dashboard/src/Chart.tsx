import type { ConfigVersion } from "./api";

const WIDTH = 480;
const HEIGHT = 200;
const LEFT = 44;
const RIGHT = 16;
const TOP = 24;
const BOTTOM = 28;

export interface ChartPoint {
  version: number;
  value: number;
  // Kept or current versions are filled; rolled back ones hollow; under test, null.
  kept: boolean | null;
}

const verdicts = (kept: boolean | null) => {
  if (kept === null) return "under test";
  return kept ? "kept" : "rolled back";
};

// One measure of each version tried on the held-out claims, from 0 to `max`, in SVG.
export function ScoreChart({
  title,
  points,
  max,
  format,
  series,
}: Readonly<{
  title: string;
  points: ChartPoint[];
  max: number;
  format: (value: number) => string;
  series: "accuracy" | "grade";
}>) {
  const step = (WIDTH - LEFT - RIGHT) / Math.max(points.length, 1);
  const x = (i: number) => LEFT + step * (i + 0.5);
  const y = (value: number) =>
    TOP + (HEIGHT - TOP - BOTTOM) * (1 - Math.min(value, max) / max);
  const described = points
    .map((p) => `version ${String(p.version)} ${format(p.value)}`)
    .join(", ");
  const line = points
    .map((p, i) => `${String(x(i))},${String(y(p.value))}`)
    .join(" ");
  return (
    <figure className="chart stack-sm">
      <figcaption>
        <h3>{title}</h3>
      </figcaption>
      <svg
        viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
        role="img"
        aria-label={`${title}: ${described}`}
      >
        {[0, max / 2, max].map((tick) => (
          <g key={tick} className="grid">
            <line x1={LEFT} x2={WIDTH - RIGHT} y1={y(tick)} y2={y(tick)} />
            <text x={LEFT - 8} y={y(tick)} dy="0.35em" textAnchor="end">
              {format(tick)}
            </text>
          </g>
        ))}
        <polyline className={`series ${series}`} points={line} />
        {points.map((p, i) => (
          <g key={p.version}>
            <circle
              className={`point ${series}${p.kept ? "" : " hollow"}`}
              cx={x(i)}
              cy={y(p.value)}
              r={5}
            >
              <title>
                Version {p.version}: {format(p.value)}, {verdicts(p.kept)}
              </title>
            </circle>
            <text className="value" x={x(i)} y={y(p.value) - 11}>
              {format(p.value)}
            </text>
            <text className="axis" x={x(i)} y={HEIGHT - 8}>
              Version {p.version}
            </text>
          </g>
        ))}
      </svg>
    </figure>
  );
}

export function VersionCharts({
  versions,
}: Readonly<{ versions: ConfigVersion[] }>) {
  // Oldest first, and only the versions with held-out scores.
  const scored = versions
    .filter((v) => v.held_out !== null)
    .sort((a, b) => a.version - b.version);
  if (scored.length === 0)
    return (
      <p className="muted">
        No version has held-out scores yet. They appear once a round has run its
        first evaluations.
      </p>
    );
  const kept = (v: ConfigVersion) => (v.current ? true : v.kept);
  return (
    <>
      <div className="charts">
        <ScoreChart
          title="Held-out accuracy"
          series="accuracy"
          max={1}
          format={(value) => `${String(Math.round(value * 100))}%`}
          points={scored.map((v) => ({
            version: v.version,
            value: v.held_out?.accuracy ?? 0,
            kept: kept(v),
          }))}
        />
        <ScoreChart
          title="Judge's grade, 0 (F) to 3 (A)"
          series="grade"
          max={3}
          format={(value) => value.toFixed(1)}
          points={scored.flatMap((v) =>
            v.held_out?.grade == null
              ? []
              : [
                  {
                    version: v.version,
                    value: v.held_out.grade,
                    kept: kept(v),
                  },
                ],
          )}
        />
      </div>
      <p className="muted small">
        A filled point is a version kept, a hollow one a version rolled back or
        still under test.
      </p>
    </>
  );
}

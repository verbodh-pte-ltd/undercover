// The lines one text changed from another, as the backend works them out with difflib:
// "+" for a line added, "-" for a line removed, " " for an unchanged line beside a change,
// and "…" for the unchanged text skipped between two changes.
function kind(line: string) {
  if (line.startsWith("+"))
    return { name: "added", sign: "+", spoken: "Added: " };
  if (line.startsWith("-"))
    return { name: "removed", sign: "−", spoken: "Removed: " };
  return { name: "same", sign: "", spoken: "" };
}

export function Diff({ lines }: Readonly<{ lines: string[] }>) {
  if (lines.length === 0)
    return <p className="muted">The text is the same as the version before.</p>;
  return (
    <ol className="diff plain" aria-label="Changed lines">
      {lines.map((line, i) => {
        const key = `${String(i)}${line}`;
        if (line === "…")
          return (
            <li key={key} className="skipped">
              <span className="diff-sign" aria-hidden="true" />
              <span>
                …<span className="hidden"> Unchanged text skipped</span>
              </span>
            </li>
          );
        const k = kind(line);
        return (
          <li key={key} className={k.name}>
            <span className="diff-sign" aria-hidden="true">
              {k.sign}
            </span>
            <span>
              {k.spoken && <span className="hidden">{k.spoken}</span>}
              {line.slice(1)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

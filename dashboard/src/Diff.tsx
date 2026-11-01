// The lines one text changed from another, as the backend works them out with difflib:
// each starts with "+" for a line added or "-" for a line removed.
export function Diff({ lines }: Readonly<{ lines: string[] }>) {
  if (lines.length === 0)
    return <p className="muted">The text is the same as the version before.</p>;
  return (
    <ol className="diff plain" aria-label="Changed lines">
      {lines.map((line, i) => {
        const added = line.startsWith("+");
        return (
          <li
            key={`${String(i)}${line}`}
            className={added ? "added" : "removed"}
          >
            <span className="diff-sign" aria-hidden="true">
              {added ? "+" : "−"}
            </span>
            <span className="hidden">{added ? "Added: " : "Removed: "}</span>
            {line.slice(1)}
          </li>
        );
      })}
    </ol>
  );
}

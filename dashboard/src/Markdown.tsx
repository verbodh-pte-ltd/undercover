// The Copilot's answers: a small, safe part of markdown, built as React elements so the model's
// text is never read as HTML. Claim ids link to the claim's page.
import type { ReactNode } from "react";

const CLAIM_ID = /^CLM-\d+/;

// A piece of inline markup starting at `at`: what it shows and where the text carries on, or
// null when the text there is plain.
interface Found {
  node: ReactNode;
  end: number;
}

// Text between an opening and a closing mark, such as `code` or **bold**.
function between(text: string, at: number, mark: string) {
  const close = text.indexOf(mark, at + mark.length);
  if (close <= at + mark.length) return null;
  return {
    inner: text.slice(at + mark.length, close),
    end: close + mark.length,
  };
}

function link(text: string, at: number): Found | null {
  const split = text.indexOf("](", at);
  const close = split === -1 ? -1 : text.indexOf(")", split);
  if (close === -1) return null;
  const label = text.slice(at + 1, split);
  const href = text.slice(split + 2, close);
  const safe = href.startsWith("#/") || href.startsWith("https://");
  return {
    node: safe ? (
      <a key={at} href={href}>
        {inline(label)}
      </a>
    ) : (
      label
    ),
    end: close + 1,
  };
}

function markupAt(text: string, at: number): Found | null {
  const rest = text.slice(at);
  if (rest.startsWith("`")) {
    const code = between(text, at, "`");
    return code && { node: <code key={at}>{code.inner}</code>, end: code.end };
  }
  if (rest.startsWith("**")) {
    const bold = between(text, at, "**");
    return (
      bold && {
        node: <strong key={at}>{inline(bold.inner)}</strong>,
        end: bold.end,
      }
    );
  }
  if (rest.startsWith("*") && !/^\*\s/.test(rest)) {
    const em = between(text, at, "*");
    return em && { node: <em key={at}>{inline(em.inner)}</em>, end: em.end };
  }
  if (rest.startsWith("[")) return link(text, at);
  const claim = CLAIM_ID.exec(rest);
  if (!claim) return null;
  return {
    node: (
      <a key={at} href={`#/claims/${claim[0]}`}>
        {claim[0]}
      </a>
    ),
    end: at + claim[0].length,
  };
}

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let plain = 0;
  let at = 0;
  while (at < text.length) {
    const found = "`*[C".includes(text[at]) ? markupAt(text, at) : null;
    if (found) {
      if (at > plain) parts.push(text.slice(plain, at));
      parts.push(found.node);
      plain = found.end;
      at = found.end;
    } else at += 1;
  }
  if (plain < text.length) parts.push(text.slice(plain));
  return parts;
}

type Block =
  | { kind: "heading" | "paragraph" | "code"; text: string }
  | { kind: "bullets" | "numbers"; items: string[] }
  | { kind: "table"; head: string[]; rows: string[][] };

const BULLET = /^\s*[-*]\s+/;
const NUMBER = /^\s*\d+[.)]\s+/;
const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());

// A table's second line, such as |---|:--:|.
const isSeparator = (line = "") =>
  line.includes("-") && cells(line).every((cell) => /^:?-+:?$/.test(cell));

// Takes the lines that match from the start, stripped of their marker.
function takeWhile(lines: string[], start: number, marker: RegExp) {
  const items: string[] = [];
  let at = start;
  while (at < lines.length && marker.test(lines[at])) {
    items.push(lines[at].replace(marker, ""));
    at += 1;
  }
  return { items, next: at };
}

function codeBlock(lines: string[], start: number) {
  const end = lines.findIndex((line, i) => i > start && line.startsWith("```"));
  const close = end === -1 ? lines.length : end;
  return {
    block: { kind: "code", text: lines.slice(start + 1, close).join("\n") },
    next: close + 1,
  } as const;
}

function paragraph(lines: string[], start: number) {
  let at = start;
  const text: string[] = [];
  while (
    at < lines.length &&
    lines[at].trim() !== "" &&
    !startsBlock(lines, at)
  ) {
    text.push(lines[at].trim());
    at += 1;
  }
  return {
    block: { kind: "paragraph", text: text.join(" ") },
    next: at,
  } as const;
}

function startsBlock(lines: string[], at: number) {
  const line = lines[at];
  return (
    line.startsWith("```") ||
    line.startsWith("#") ||
    BULLET.test(line) ||
    NUMBER.test(line) ||
    (line.trim().startsWith("|") && isSeparator(lines[at + 1]))
  );
}

function nextBlock(
  lines: string[],
  at: number,
): { block: Block; next: number } {
  const line = lines[at];
  if (line.startsWith("```")) return codeBlock(lines, at);
  if (line.startsWith("#"))
    return {
      block: { kind: "heading", text: line.replace(/^#+\s*/, "") },
      next: at + 1,
    };
  if (BULLET.test(line)) {
    const { items, next } = takeWhile(lines, at, BULLET);
    return { block: { kind: "bullets", items }, next };
  }
  if (NUMBER.test(line)) {
    const { items, next } = takeWhile(lines, at, NUMBER);
    return { block: { kind: "numbers", items }, next };
  }
  if (line.trim().startsWith("|") && isSeparator(lines[at + 1])) {
    const rows = takeWhile(lines, at + 2, /^\s*\|/);
    return {
      block: {
        kind: "table",
        head: cells(line),
        rows: rows.items.map((row) => cells(`|${row}`)),
      },
      next: rows.next,
    };
  }
  return paragraph(lines, at);
}

function blocks(text: string): Block[] {
  const lines = text.replaceAll("\r\n", "\n").split("\n");
  const found: Block[] = [];
  let at = 0;
  while (at < lines.length) {
    if (lines[at].trim() === "") {
      at += 1;
      continue;
    }
    const { block, next } = nextBlock(lines, at);
    found.push(block);
    at = next;
  }
  return found;
}

function BlockView({ block }: Readonly<{ block: Block }>) {
  switch (block.kind) {
    case "heading":
      return <h4>{inline(block.text)}</h4>;
    case "paragraph":
      return <p>{inline(block.text)}</p>;
    case "code":
      return <pre>{block.text}</pre>;
    case "bullets":
    case "numbers": {
      const List = block.kind === "bullets" ? "ul" : "ol";
      return (
        <List>
          {block.items.map((item, i) => (
            <li key={i}>{inline(item)}</li>
          ))}
        </List>
      );
    }
    case "table":
      return (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {block.head.map((cell, i) => (
                  <th key={i}>{inline(cell)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, i) => (
                    <td key={i}>{inline(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
  }
}

export function Markdown({ text }: Readonly<{ text: string }>) {
  return (
    <div className="markdown">
      {blocks(text).map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}

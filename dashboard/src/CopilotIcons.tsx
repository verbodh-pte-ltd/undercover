// The Copilot's icons: simple line drawings in the text's color.
import type { ReactNode } from "react";
import type { CopilotStepKind } from "./copilotApi";

function Icon({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

const KIND_PATHS: Record<CopilotStepKind, ReactNode> = {
  // A page with lines, for claims.
  claims: (
    <>
      <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <path d="M14 3v6h6M8 13h8M8 17h5" />
    </>
  ),
  // A person, for customers and policies.
  customers: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </>
  ),
  // An open book, for the context layer.
  context: (
    <>
      <path d="M2 5h6a4 4 0 0 1 4 4v12a3 3 0 0 0-3-3H2z" />
      <path d="M22 5h-6a4 4 0 0 0-4 4v12a3 3 0 0 1 3-3h7z" />
    </>
  ),
  // Stacked layers, for the agent's work.
  work: (
    <>
      <path d="m12 3 9 5-9 5-9-5z" />
      <path d="m3 13 9 5 9-5" />
    </>
  ),
  // A bookmark, for memory.
  memory: <path d="M6 3h12v18l-6-4-6 4z" />,
  // A bolt, for an action that changes something.
  action: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
};

export function KindIcon({ kind }: Readonly<{ kind: CopilotStepKind }>) {
  return <Icon>{KIND_PATHS[kind]}</Icon>;
}

// The Copilot's own icon: a speech bubble with a spark.
export function CopilotIcon() {
  return (
    <Icon>
      <path d="M21 12a8 8 0 0 1-11.6 7.1L4 21l1.9-5.4A8 8 0 1 1 21 12z" />
      <path d="M12 8v8M8 12h8" />
    </Icon>
  );
}

export function CloseIcon() {
  return (
    <Icon>
      <path d="m9 6 6 6-6 6" />
    </Icon>
  );
}

export function SendIcon() {
  return (
    <Icon>
      <path d="M12 19V5M5 12l7-7 7 7" />
    </Icon>
  );
}

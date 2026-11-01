import { useEffect, useRef, type ReactNode } from "react";

// The dashboard's own confirmation, asked before any action that changes the agent or a setting.
export function Confirm({
  title,
  action,
  onConfirm,
  onCancel,
  children,
}: Readonly<{
  title: string;
  action: string;
  onConfirm: () => void;
  onCancel: () => void;
  children: ReactNode;
}>) {
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirm.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
    };
  }, [onCancel]);

  return (
    <div className="overlay">
      <div
        className="panel dialog stack-sm"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
      >
        <h2 id="confirm-title">{title}</h2>
        {children}
        <div className="actions">
          <button type="button" className="secondary" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" ref={confirm} onClick={onConfirm}>
            {action}
          </button>
        </div>
      </div>
    </div>
  );
}

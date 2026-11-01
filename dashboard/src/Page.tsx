import type { ReactNode } from "react";

// Every page's header: its title and one line on what the page is for.
export function PageHeader({
  title,
  children,
}: Readonly<{ title: ReactNode; children: ReactNode }>) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      <p className="muted">{children}</p>
    </header>
  );
}

export function LoadFailed({ what }: Readonly<{ what: string }>) {
  return (
    <p className="refusal" role="alert">
      {what} couldn't be loaded. Check that the backend is running, then reload
      the page.
    </p>
  );
}

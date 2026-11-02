import { useEffect, useState } from "react";
import { getSignedInHandler, type Handler } from "./api";
import { auth } from "./auth";
import { ClaimView } from "./ClaimView";
import { Configuration } from "./Configuration";
import { Improvement } from "./Improvement";
import { Queue } from "./Queue";
import { EvaluationView, Scoreboard } from "./Scoreboard";
import { Settings } from "./Settings";
import { SignIn } from "./SignIn";

function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const update = () => {
      setHash(window.location.hash);
    };
    window.addEventListener("hashchange", update);
    return () => {
      window.removeEventListener("hashchange", update);
    };
  }, []);
  return hash;
}

// The signed-in user: undefined until the saved sign-in has been read, then null when signed out.
function useUser() {
  const [user, setUser] = useState<string | null | undefined>();
  useEffect(() => auth.onChange(setUser), []);
  return user;
}

function Page({ hash, handler }: Readonly<{ hash: string; handler: Handler }>) {
  const claim = /^#\/claims\/(.+)$/.exec(hash);
  if (claim)
    return <ClaimView key={claim[1]} claimId={claim[1]} handler={handler} />;
  const evaluation = /^#\/scoreboard\/(\d+)$/.exec(hash);
  if (evaluation)
    return <EvaluationView key={evaluation[1]} runId={evaluation[1]} />;
  if (hash === "#/scoreboard") return <Scoreboard />;
  // The Configuration page took the place of the Instructions page.
  if (hash === "#/configuration" || hash === "#/instructions")
    return <Configuration />;
  if (hash === "#/settings") return <Settings handler={handler} />;
  if (hash === "#/improvement") return <Improvement handler={handler} />;
  return <Queue />;
}

const signOut = () => void auth.signOut();

const NAV = [
  ["#/", "Queue"],
  ["#/scoreboard", "Scoreboard"],
  ["#/improvement", "Improvement"],
  ["#/configuration", "Configuration"],
  ["#/settings", "Settings"],
];

// The page the address is on, for the navigation; a claim belongs to the queue.
function here(hash: string) {
  if (hash === "#/instructions") return "#/configuration";
  const found = NAV.find(([href]) => href !== "#/" && hash.startsWith(href));
  return found ? found[0] : "#/";
}

function SignedIn() {
  const hash = useHash();
  const [handler, setHandler] = useState<Handler | null>(null);
  const [refused, setRefused] = useState<string | null>(null);

  useEffect(() => {
    getSignedInHandler().then(setHandler, (error: unknown) => {
      setRefused(error instanceof Error ? error.message : String(error));
    });
  }, []);

  return (
    <>
      <nav className="topbar">
        <a className="brand" href="#/">
          UnderCover <span className="tagline">claims desk</span>
        </a>
        {NAV.map(([href, text]) => (
          <a
            key={href}
            href={href}
            aria-current={here(hash) === href ? "page" : undefined}
          >
            {text}
          </a>
        ))}
        <span className="who">
          {handler && <span>{handler.name}</span>}
          <button type="button" className="quiet" onClick={signOut}>
            Sign out
          </button>
        </span>
      </nav>
      <main>
        {refused && (
          <p className="refusal" role="alert">
            {refused}
          </p>
        )}
        {handler && <Page hash={hash} handler={handler} />}
      </main>
    </>
  );
}

export function App() {
  const user = useUser();
  if (user === undefined) return null;
  return user ? <SignedIn key={user} /> : <SignIn />;
}

import type { Session } from "@supabase/supabase-js";
import { useEffect, useState } from "react";
import { getSignedInHandler, type Handler } from "./api";
import { ClaimView } from "./ClaimView";
import { Instructions } from "./Instructions";
import { Queue } from "./Queue";
import { EvaluationView, Scoreboard } from "./Scoreboard";
import { SignIn } from "./SignIn";
import { supabase } from "./supabase";

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

// The saved sign-in: undefined until it has been read, then null when signed out.
function useSession() {
  const [session, setSession] = useState<Session | null | undefined>();
  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((_event, changed) => {
      setSession(changed);
    });
    return () => {
      data.subscription.unsubscribe();
    };
  }, []);
  return session;
}

function Page({ hash, handler }: Readonly<{ hash: string; handler: Handler }>) {
  const claim = /^#\/claims\/(.+)$/.exec(hash);
  if (claim)
    return <ClaimView key={claim[1]} claimId={claim[1]} handler={handler} />;
  const evaluation = /^#\/scoreboard\/(\d+)$/.exec(hash);
  if (evaluation)
    return <EvaluationView key={evaluation[1]} runId={evaluation[1]} />;
  if (hash === "#/scoreboard") return <Scoreboard />;
  if (hash === "#/instructions") return <Instructions />;
  return <Queue />;
}

const signOut = () => void supabase.auth.signOut();

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
        <a href="#/">Queue</a>
        <a href="#/scoreboard">Scoreboard</a>
        <a href="#/instructions">Instructions</a>
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
  const session = useSession();
  if (session === undefined) return null;
  return session ? <SignedIn key={session.user.id} /> : <SignIn />;
}

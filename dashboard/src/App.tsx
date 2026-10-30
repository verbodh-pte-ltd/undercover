import { useEffect, useState } from "react";
import { ClaimView } from "./ClaimView";
import { Instructions } from "./Instructions";
import { Queue } from "./Queue";
import { EvaluationView, Scoreboard } from "./Scoreboard";

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

function Page({ hash }: Readonly<{ hash: string }>) {
  const claim = /^#\/claims\/(.+)$/.exec(hash);
  if (claim) return <ClaimView key={claim[1]} claimId={claim[1]} />;
  const evaluation = /^#\/scoreboard\/(\d+)$/.exec(hash);
  if (evaluation)
    return <EvaluationView key={evaluation[1]} runId={evaluation[1]} />;
  if (hash === "#/scoreboard") return <Scoreboard />;
  if (hash === "#/instructions") return <Instructions />;
  return <Queue />;
}

export function App() {
  const hash = useHash();
  return (
    <>
      <nav className="topbar">
        <a className="brand" href="#/">
          UnderCover <span className="tagline">claims desk</span>
        </a>
        <a href="#/">Queue</a>
        <a href="#/scoreboard">Scoreboard</a>
        <a href="#/instructions">Instructions</a>
      </nav>
      <main>
        <Page hash={hash} />
      </main>
    </>
  );
}

import { useEffect, useState } from "react";
import { getInstructions, type Instructions as Texts } from "./api";

export function Instructions() {
  const [texts, setTexts] = useState<Texts | null>(null);

  useEffect(() => {
    void getInstructions().then(setTexts);
  }, []);

  return (
    <section className="stack">
      <h1>The agent's instructions</h1>
      <p className="muted">
        Read only. These are the instructions the claim agent works from. The
        dispatcher that hands out the claims follows fixed rules and uses no
        model.
      </p>
      <h2>Working a claim</h2>
      <pre className="panel instructions">{texts?.claim ?? "Loading"}</pre>
    </section>
  );
}

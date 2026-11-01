import { useState } from "react";
import { supabase } from "./supabase";

export function SignIn() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  return (
    <main className="sign-in">
      <form
        className="panel stack-sm"
        onSubmit={(event) => {
          event.preventDefault();
          setBusy(true);
          void supabase.auth
            .signInWithPassword({ email, password })
            .then(({ error: refused }) => {
              setBusy(false);
              if (!refused) return;
              setError(
                refused.code === "invalid_credentials"
                  ? "That email and password don't match. Check them and try again."
                  : `${refused.message}. Try again in a moment.`,
              );
            });
        }}
      >
        <h1>
          UnderCover <span className="tagline">claims desk</span>
        </h1>
        <p className="muted">Sign in with your work email and password.</p>
        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
          }}
        />
        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />
        <button type="submit" disabled={busy}>
          Sign in
        </button>
        {error && (
          <p className="refusal" role="alert">
            {error}
          </p>
        )}
      </form>
    </main>
  );
}

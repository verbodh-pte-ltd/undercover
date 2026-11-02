import { useState } from "react";
import { auth } from "./auth";

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
          void auth.signIn(email, password).then((refused) => {
            setBusy(false);
            setError(refused);
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

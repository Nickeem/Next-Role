import { useId, useState, type FormEvent } from "react";

import { api } from "../api";

export function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const passwordId = useId();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className="page page--center">
      <main className="card login">
        <div className="rail__logo" aria-hidden="true">
          NR
        </div>
        <h1>Next-Role review</h1>
        <p className="muted">Sign in to review your assessed roles.</p>
        <form onSubmit={submit}>
          <div className="field">
            <label htmlFor={passwordId}>Password</label>
            <input
              id={passwordId}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoFocus
            />
          </div>
          {error && (
            <p className="notice notice--error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </main>
    </div>
  );
}

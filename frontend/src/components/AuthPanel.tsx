import { useState, type FormEvent } from "react";
import { API_BASE, login, signup } from "../api";
import { setToken } from "../auth";
import type { AuthUser } from "../types";

interface Props {
  onAuthenticated: (token: string, user: AuthUser) => void;
  onBack: () => void;
}

export default function AuthPanel({ onAuthenticated, onBack }: Props) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const result =
        mode === "login"
          ? await login(username.trim(), password)
          : await signup(username.trim(), password, email.trim() || undefined);
      setToken(result.token);
      onAuthenticated(result.token, result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  function handleGoogle() {
    window.location.href = `${API_BASE}/api/auth/google/login`;
  }

  return (
    <div className="auth-panel">
      <button type="button" className="auth-panel__back" onClick={onBack}>
        ← Back
      </button>

      <div className="auth-panel__tabs">
        <button
          type="button"
          className={`auth-panel__tab${mode === "login" ? " auth-panel__tab--active" : ""}`}
          onClick={() => setMode("login")}
        >
          Log in
        </button>
        <button
          type="button"
          className={`auth-panel__tab${mode === "signup" ? " auth-panel__tab--active" : ""}`}
          onClick={() => setMode("signup")}
        >
          Sign up
        </button>
      </div>

      <form className="auth-panel__form" onSubmit={handleSubmit}>
        <label className="field">
          <span className="field__label">Username</span>
          <input
            type="text"
            className="field__input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
        </label>

        <label className="field">
          <span className="field__label">Password</span>
          <input
            type="password"
            className="field__input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </label>

        {mode === "signup" && (
          <label className="field">
            <span className="field__label">
              Email <span className="field__hint">(optional)</span>
            </span>
            <input
              type="email"
              className="field__input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />
          </label>
        )}

        {error && <p className="error">{error}</p>}

        <button
          type="submit"
          className="input-panel__submit"
          disabled={submitting || !username.trim() || !password}
        >
          {submitting ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
        </button>
      </form>

      <div className="auth-panel__divider">or</div>

      <button type="button" className="auth-panel__google" onClick={handleGoogle}>
        Continue with Google
      </button>
    </div>
  );
}

/**
 * Login — shown by <App> when auth is enforced and there is no session.
 */
import { useState } from "react";
import synoraLogo from "../assets/Synora Logo.png";
import { useAuth } from "../hooks/useAuth";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username.trim(), password);
      // AuthProvider state flips to authed; App re-renders the app.
    } catch (err) {
      setError(
        err?.status === 429
          ? "Too many attempts. Please wait a few minutes and try again."
          : err?.message || "Login failed",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4 font-sans">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center mb-8">
          <div className="w-14 h-14 rounded-2xl overflow-hidden shadow-lg shadow-primary/20 bg-white ring-1 ring-primary/10 mb-3">
            <img src={synoraLogo} alt="Iris by Synora AI Labs" className="w-full h-full object-cover" />
          </div>
          <h1 className="text-2xl font-extrabold text-text-main">
            <span className="text-gradient">Iris</span>
          </h1>
          <p className="text-xs font-medium text-text-muted mt-1">by Synora AI Labs</p>
        </div>

        <form
          onSubmit={onSubmit}
          className="bg-card rounded-2xl shadow-md border border-primary/8 p-6 flex flex-col gap-4"
        >
          <h2 className="text-lg font-bold text-text-main">Sign in</h2>

          {error && (
            <div className="text-sm font-semibold text-danger bg-danger/10 border border-danger/20 rounded-xl px-3 py-2">
              {error}
            </div>
          )}

          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-bold text-text-muted uppercase tracking-wide">Username</span>
            <input
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              autoFocus
              className="px-3.5 py-2.5 rounded-xl bg-background border border-primary/10 text-text-main outline-none focus:border-primary/40 transition-colors"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-bold text-text-muted uppercase tracking-wide">Password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="px-3.5 py-2.5 rounded-xl bg-background border border-primary/10 text-text-main outline-none focus:border-primary/40 transition-colors"
            />
          </label>

          <button
            type="submit"
            disabled={busy}
            className="mt-1 px-4 py-2.5 rounded-xl bg-primary text-white font-bold shadow-sm hover:bg-primary/90 transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {busy && (
              <span className="w-4 h-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            )}
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}

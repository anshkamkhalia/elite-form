import { useState } from "react";
import { useAuth } from "../context/AuthContext";
import { IconBall, IconAlert } from "./icons";

export default function AuthScreen() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (isRegister) {
        await register(username.trim(), password);
      } else {
        await login(username.trim(), password);
      }
    } catch (err) {
      setError(err.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const switchMode = (next) => {
    setMode(next);
    setError(null);
  };

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="brand-mark">
            <IconBall size={20} />
          </span>
          <div>
            <div className="brand-name">EliteForm</div>
            <div className="brand-sub">Computer Vision Tennis Analysis</div>
          </div>
        </div>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={!isRegister}
            className={!isRegister ? "active" : ""}
            onClick={() => switchMode("login")}
          >
            Sign in
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={isRegister}
            className={isRegister ? "active" : ""}
            onClick={() => switchMode("register")}
          >
            Create account
          </button>
        </div>

        <form className="auth-form" onSubmit={submit}>
          <div className="field">
            <label htmlFor="auth-username">Username</label>
            <input
              id="auth-username"
              type="text"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="3–32 letters, numbers, or underscores"
              required
              autoFocus
            />
          </div>
          <div className="field">
            <label htmlFor="auth-password">Password</label>
            <input
              id="auth-password"
              type="password"
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isRegister ? "At least 8 characters" : "Your password"}
              required
            />
          </div>

          {error && (
            <div className="alert alert-error" role="alert">
              <IconAlert size={16} />
              <div>{error}</div>
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary btn-block"
            disabled={busy || !username || !password}
          >
            {busy
              ? "Please wait…"
              : isRegister
              ? "Create account"
              : "Sign in"}
          </button>
        </form>

        <p className="auth-foot">
          {isRegister ? (
            <>
              Already have an account?{" "}
              <button type="button" className="link" onClick={() => switchMode("login")}>
                Sign in
              </button>
            </>
          ) : (
            <>
              New here?{" "}
              <button type="button" className="link" onClick={() => switchMode("register")}>
                Create an account
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}

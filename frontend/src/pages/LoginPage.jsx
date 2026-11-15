import { useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../api/auth.jsx";

export default function LoginPage() {
  const { user, login } = useAuth();
  const nav = useNavigate();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/" replace />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await login(u, p);
      nav(res.must_change_password ? "/change-password" : "/", { replace: true });
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <div className="brand big"><span className="hex">⬢</span> TheHive</div>
        <p className="muted">Several models. One verdict. Local only.</p>
        <label>Username<input value={u} onChange={(e) => setU(e.target.value)} autoFocus autoComplete="username" /></label>
        <label>Password<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" /></label>
        {err && <div className="error">{err}</div>}
        <button disabled={busy || !u || !p}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { auth } from "../api/client.js";
import { useAuth } from "../api/auth.jsx";

export default function ChangePasswordPage() {
  const { refresh } = useAuth();
  const nav = useNavigate();
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [again, setAgain] = useState("");
  const [err, setErr] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    if (nw !== again) return setErr("Passwords do not match");
    if (nw.length < 12) return setErr("Use at least 12 characters");
    try { await auth.changePassword(cur, nw); await refresh(); nav("/", { replace: true }); }
    catch (ex) { setErr(ex.message); }
  };

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <h2>Set a new password</h2>
        <p className="muted">Required on first sign-in and after an admin reset.</p>
        <label>Current password<input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoFocus /></label>
        <label>New password<input type="password" value={nw} onChange={(e) => setNw(e.target.value)} /></label>
        <label>Repeat new password<input type="password" value={again} onChange={(e) => setAgain(e.target.value)} /></label>
        {err && <div className="error">{err}</div>}
        <button disabled={!cur || !nw || !again}>Save</button>
      </form>
    </div>
  );
}

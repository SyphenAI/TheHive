import { useEffect, useState } from "react";
import { auth, when } from "../api/client.js";

export default function AccessPage() {
  const [users, setUsers] = useState([]);
  const [keys, setKeys] = useState([]);
  const [err, setErr] = useState("");
  const [fresh, setFresh] = useState(null); // plaintext key, shown once
  const [uf, setUf] = useState({ username: "", display_name: "", password: "", role: "member" });
  const [kf, setKf] = useState({ name: "", scopes: ["read"], expires_days: 90 });

  const load = () => Promise.all([auth.users(), auth.apiKeys()]).then(([u, k]) => { setUsers(u); setKeys(k); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  const run = async (fn) => { setErr(""); try { await fn(); await load(); } catch (e) { setErr(e.message); } };

  const addUser = (e) => { e.preventDefault(); run(() => auth.createUser(uf)).then(() => setUf({ username: "", display_name: "", password: "", role: "member" })); };
  const addKey = async (e) => {
    e.preventDefault(); setErr("");
    try { const r = await auth.createApiKey({ ...kf, expires_days: kf.expires_days ? Number(kf.expires_days) : null }); setFresh(r); setKf({ name: "", scopes: ["read"], expires_days: 90 }); await load(); }
    catch (ex) { setErr(ex.message); }
  };
  const toggleScope = (s) => setKf((k) => ({ ...k, scopes: k.scopes.includes(s) ? k.scopes.filter((x) => x !== s) : [...k.scopes, s] }));
  const reset = (u) => { const pw = prompt(`Temporary password for ${u.username} (12+ chars). They must change it on next login.`); if (pw) run(() => auth.resetPassword(u.id, pw)); };

  return (
    <div>
      <h1>Users &amp; API keys</h1>
      {err && <div className="error">{err}</div>}
      {fresh && (
        <div className="card verdict">
          <h3>New API key: {fresh.name}</h3>
          <p className="warn">Copy it now. It is not stored and cannot be shown again.</p>
          <pre className="wrap"><code>{fresh.key}</code></pre>
          <p className="small muted">Use it as <code>X-API-Key: {fresh.key.slice(0, 16)}…</code> against <code>/api/v1/hive/*</code>. Scopes: {fresh.scopes}.</p>
          <button onClick={() => setFresh(null)}>I've saved it</button>
        </div>
      )}
      <div className="two-col">
        <section>
          <h2>Users</h2>
          {users.map((u) => (
            <div key={u.id} className={`card ${u.is_active ? "" : "off"}`}>
              <div className="row between"><strong>{u.display_name || u.username}</strong><span className="pill">{u.role}</span></div>
              <div className="small muted">{u.username} · {u.is_active ? "active" : "deactivated"}{u.must_change_password ? " · must change password" : ""}</div>
              <div className="row actions">
                <button onClick={() => reset(u)}>Reset password</button>
                {u.is_active && <button className="danger" onClick={() => confirm(`Deactivate ${u.username}?`) && run(() => auth.deactivate(u.id))}>Deactivate</button>}
              </div>
            </div>
          ))}
          <form className="card" onSubmit={addUser}>
            <h3>Add user</h3>
            <div className="row">
              <label>Username<input required value={uf.username} onChange={(e) => setUf({ ...uf, username: e.target.value })} /></label>
              <label>Display name<input value={uf.display_name} onChange={(e) => setUf({ ...uf, display_name: e.target.value })} /></label>
            </div>
            <div className="row">
              <label>Temporary password<input type="password" required minLength={12} value={uf.password} onChange={(e) => setUf({ ...uf, password: e.target.value })} autoComplete="new-password" /></label>
              <label>Role<select value={uf.role} onChange={(e) => setUf({ ...uf, role: e.target.value })}><option value="member">member</option><option value="admin">admin</option></select></label>
            </div>
            <button>Create</button>
          </form>
        </section>
        <section>
          <h2>API keys</h2>
          <p className="muted small">For integrations. <b>read</b>: observe sessions/usage. <b>run</b>: start sessions. <b>admin</b>: manage providers &amp; seats. Keys can never manage users or other keys.</p>
          {keys.map((k) => (
            <div key={k.id} className={`card ${k.revoked ? "off" : ""}`}>
              <div className="row between"><strong>{k.name}</strong><code>hive_sk_{k.prefix}_…</code></div>
              <div className="small muted">[{k.scopes}] · expires {when(k.expires_at)} · last used {when(k.last_used_at)}{k.revoked ? " · REVOKED" : ""}</div>
              {!k.revoked && <div className="row actions"><button className="danger" onClick={() => confirm(`Revoke ${k.name}?`) && run(() => auth.revokeApiKey(k.id))}>Revoke</button></div>}
            </div>
          ))}
          <form className="card" onSubmit={addKey}>
            <h3>Create API key</h3>
            <label>Name<input required value={kf.name} onChange={(e) => setKf({ ...kf, name: e.target.value })} placeholder="e.g. threat-model-pipeline" /></label>
            <div className="row">
              {["read", "run", "admin"].map((s) => <label key={s} className={`chip ${kf.scopes.includes(s) ? "on" : ""}`}><input type="checkbox" checked={kf.scopes.includes(s)} onChange={() => toggleScope(s)} />{s}</label>)}
              <label>Expires in (days)<input type="number" min={1} max={3650} value={kf.expires_days} onChange={(e) => setKf({ ...kf, expires_days: e.target.value })} /></label>
            </div>
            <button disabled={!kf.name || kf.scopes.length === 0}>Create key</button>
          </form>
        </section>
      </div>
    </div>
  );
}

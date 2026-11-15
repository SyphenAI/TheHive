import { useEffect, useState } from "react";
import { system, when } from "../api/client.js";

export default function AuditPage() {
  const [rows, setRows] = useState([]);
  const [health, setHealth] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    system.audit(200).then((r) => setRows(r.events)).catch((e) => setErr(e.message));
    system.health().then(setHealth).catch(() => {});
  }, []);
  return (
    <div>
      <h1>Audit</h1>
      {health && <div className="card small"><strong>{health.status}</strong> · v{health.version} · {health.app_env} · {Object.entries(health.checks).filter(([k]) => k !== "ok").map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</div>}
      {err && <div className="error">{err}</div>}
      <table className="scores">
        <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Detail</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.id}><td className="small muted">{when(r.created_at)}</td><td>{r.actor}</td><td><code>{r.action}</code></td><td className="small">{r.detail}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

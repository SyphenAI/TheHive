import { useEffect, useState } from "react";
import { hive, usd } from "../api/client.js";

export default function UsagePage() {
  const [days, setDays] = useState(30);
  const [u, setU] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { hive.usage(days).then(setU).catch((e) => setErr(e.message)); }, [days]);
  if (err) return <div className="error">{err}</div>;
  if (!u) return <div className="muted">Loading…</div>;
  return (
    <div>
      <div className="row between"><h1>Usage</h1>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>{[7, 30, 90, 365].map((d) => <option key={d} value={d}>{d} days</option>)}</select></div>
      <div className="row stats">
        <div className="card stat"><div className="muted small">Period total</div><div className="num">{usd(u.total_usd)}</div></div>
        <div className="card stat"><div className="muted small">Today</div><div className="num">{usd(u.daily_spent_usd)}</div><div className="muted small">cap ${u.daily_limit_usd}</div></div>
        <div className="card stat"><div className="muted small">This month</div><div className="num">{usd(u.monthly_spent_usd)}</div><div className="muted small">cap ${u.monthly_limit_usd}</div></div>
      </div>
      <p className="muted small">Costs are estimates from registry/providers.json prices × reported token counts. Treat vendor invoices as truth.</p>
      <table className="scores">
        <thead><tr><th>Provider</th><th>Model</th><th>Calls</th><th>Tokens in</th><th>Tokens out</th><th>Cost</th></tr></thead>
        <tbody>{u.by_model.sort((a, b) => b.cost_usd - a.cost_usd).map((r) => (
          <tr key={r.provider + r.model}><td>{r.provider}</td><td>{r.model}</td><td>{r.calls}</td><td>{r.tokens_in.toLocaleString()}</td><td>{r.tokens_out.toLocaleString()}</td><td>{usd(r.cost_usd)}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

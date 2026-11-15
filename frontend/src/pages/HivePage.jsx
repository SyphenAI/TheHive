import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { hive, providers, usd, when } from "../api/client.js";

const STATUS = { pending: "⏳", running: "🐝", done: "✅", halted: "⛔", error: "💥" };
const EMPTY = { title: "", problem: "", context: "", task_type: "general", max_rounds: 3, budget_usd: 1.0, judge_member_id: "" };

export default function HivePage() {
  const nav = useNavigate();
  const loc = useLocation();
  const [sessions, setSessions] = useState([]);
  const [members, setMembers] = useState([]);
  const [presets, setPresets] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ ...EMPTY, ...(loc.state?.rerun || {}) });
  const [picked, setPicked] = useState(new Set(loc.state?.rerun?.member_ids || []));
  const [est, setEst] = useState(null);
  const estTimer = useRef(null);

  const load = () => Promise.all([hive.sessions(), providers.members(), hive.presets()])
    .then(([s, m, p]) => { setSessions(s); setMembers(m); setPresets(p); setPicked((cur) => cur.size ? cur : new Set(m.filter((x) => x.enabled).map((x) => x.id))); })
    .catch((e) => setErr(e.message));
  useEffect(() => { load(); const t = setInterval(() => hive.sessions().then(setSessions).catch(() => {}), 5000); return () => clearInterval(t); }, []); // eslint-disable-line

  // Live cost estimate, debounced.
  useEffect(() => {
    clearTimeout(estTimer.current);
    if (picked.size < 2) { setEst(null); return; }
    estTimer.current = setTimeout(() => {
      hive.estimate({ problem: form.problem, context: form.context, max_rounds: Number(form.max_rounds), member_ids: [...picked] }).then(setEst).catch(() => setEst(null));
    }, 400);
    return () => clearTimeout(estTimer.current);
  }, [form.problem, form.context, form.max_rounds, picked]);

  const toggle = (id) => setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const answering = members.filter((m) => picked.has(m.id) && m.enabled && m.can_answer).length;
  const overBudget = est && est.worst_case_usd > Number(form.budget_usd);
  const overGlobal = est && est.worst_case_usd > (est.global.daily_limit_usd - est.global.daily_spent_usd);

  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr("");
    try {
      const s = await hive.create({ ...form, max_rounds: Number(form.max_rounds), budget_usd: Number(form.budget_usd), member_ids: [...picked], judge_member_id: form.judge_member_id ? Number(form.judge_member_id) : null });
      nav(`/sessions/${s.id}`);
    } catch (ex) { setErr(ex.message); } finally { setBusy(false); }
  };

  const applyPreset = (p) => {
    setPicked(new Set(p.member_ids));
    setForm((f) => ({ ...f, task_type: p.task_type, max_rounds: p.max_rounds, budget_usd: p.budget_usd, judge_member_id: p.judge_member_id || "" }));
  };
  const savePreset = async () => {
    const name = prompt("Preset name (existing name overwrites):");
    if (!name) return;
    try { await hive.savePreset({ name, member_ids: [...picked], judge_member_id: form.judge_member_id ? Number(form.judge_member_id) : null, task_type: form.task_type, max_rounds: Number(form.max_rounds), budget_usd: Number(form.budget_usd) }); setPresets(await hive.presets()); }
    catch (ex) { setErr(ex.message); }
  };
  const delPreset = async (p) => { if (confirm(`Delete preset "${p.name}"?`)) { await hive.deletePreset(p.id); setPresets(await hive.presets()); } };

  return (
    <div className="two-col">
      <section>
        <h1>New hive session</h1>
        <div className="stateless"><span className="hex">⬢</span> <strong>Stateless.</strong> The seats see only this problem, this context, and each other's turns in this session. Nothing from earlier sessions, files, or chats is ever included.</div>
        {members.length < 2 && <div className="warn">Add at least two seats under <Link to="/providers">Providers &amp; seats</Link> before running a hive.</div>}
        {(presets.length > 0 || members.length >= 2) && (
          <div className="row presets">
            <span className="muted small">Presets:</span>
            {presets.map((p) => <span key={p.id} className="chip preset"><button className="link" onClick={() => applyPreset(p)}>{p.name}</button><button className="link x" title="delete" onClick={() => delPreset(p)}>×</button></span>)}
            {picked.size >= 2 && <button className="link small" onClick={savePreset}>+ save current</button>}
          </div>
        )}
        <form className="card" onSubmit={submit}>
          <label>Title <span className="muted small">(optional)</span><input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
          <label>Problem<textarea rows={7} required value={form.problem} onChange={(e) => setForm({ ...form, problem: e.target.value })} placeholder="What should the hive work on?" /></label>
          <label>Context <span className="muted small">(prior work, code, notes — treated as data, never instructions)</span>
            <textarea rows={5} value={form.context} onChange={(e) => setForm({ ...form, context: e.target.value })} /></label>
          <div className="row">
            <label>Task type
              <select value={form.task_type} onChange={(e) => setForm({ ...form, task_type: e.target.value })}>
                <option value="general">General</option><option value="code">Code</option><option value="security">Security</option>
              </select></label>
            <label>Max rounds<input type="number" min={1} max={5} value={form.max_rounds} onChange={(e) => setForm({ ...form, max_rounds: e.target.value })} /></label>
            <label>Budget (USD)<input type="number" min={0.05} step={0.05} value={form.budget_usd} onChange={(e) => setForm({ ...form, budget_usd: e.target.value })} /></label>
          </div>
          <fieldset>
            <legend>Seats ({answering} answering)</legend>
            {members.map((m) => (
              <label key={m.id} className={`chip ${picked.has(m.id) ? "on" : ""} ${m.enabled ? "" : "off"}`}>
                <input type="checkbox" disabled={!m.enabled} checked={picked.has(m.id)} onChange={() => toggle(m.id)} />
                {m.nickname || `${m.provider}/${m.model}`}
                <span className="muted small"> {[m.can_answer && "A", m.can_critique && "C", m.can_judge && "J", m.can_redteam && "R"].filter(Boolean).join("")}</span>
              </label>
            ))}
          </fieldset>
          <label>Judge
            <select value={form.judge_member_id} onChange={(e) => setForm({ ...form, judge_member_id: e.target.value })}>
              <option value="">Auto (rotate; prefers a seat that did not answer)</option>
              {members.filter((m) => m.can_judge && m.enabled).map((m) => <option key={m.id} value={m.id}>{m.nickname || `${m.provider}/${m.model}`}</option>)}
            </select></label>

          {est && (
            <div className={`estimate ${overBudget || overGlobal ? "bad" : ""}`}>
              <div className="row between">
                <span><strong>Worst case {usd(est.worst_case_usd)}</strong> <span className="muted">· likely ≈ {usd(est.likely_usd)} · {est.calls} calls</span></span>
                <details className="small"><summary>breakdown</summary>
                  <table className="scores small"><tbody>{est.phases.map((p) => <tr key={p.phase}><td>{p.phase}</td><td>{p.calls}×</td><td>{usd(p.usd)}</td></tr>)}</tbody></table>
                </details>
              </div>
              {overBudget && <div className="small">Worst case exceeds this session's ${form.budget_usd} budget — the run would halt partway. Raise the budget or drop rounds/seats.</div>}
              {overGlobal && <div className="small">Worst case exceeds what's left of today's global ceiling ({usd(est.global.daily_limit_usd - est.global.daily_spent_usd)}).</div>}
              {est.seats.some((s) => !s.known_price) && <div className="small muted">Some seats use models not in the registry; priced at the provider default.</div>}
            </div>
          )}
          {err && <div className="error">{err}</div>}
          <button disabled={busy || answering < 2 || !form.problem.trim()}>{busy ? "Starting…" : "Release the hive"}</button>
        </form>
      </section>
      <section>
        <h1>Recent sessions</h1>
        <div className="list">
          {sessions.length === 0 && <div className="muted">Nothing yet.</div>}
          {sessions.map((s) => (
            <Link key={s.id} to={`/sessions/${s.id}`} className="card row-card">
              <span className="status">{STATUS[s.status] || "•"}</span>
              <span className="grow"><div>{s.title}</div><div className="muted small">{s.task_type} · {when(s.created_at)}</div></span>
              <span className="muted small">{usd(s.spent_usd)} / ${s.budget_usd}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

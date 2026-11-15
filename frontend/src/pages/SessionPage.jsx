import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { hive, providers, usd, when } from "../api/client.js";

const PHASE_LABEL = { answer: "Blind answer", critique: "Critique", revise: "Revision", judge: "Verdict" };

export default function SessionPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [s, setS] = useState(null);
  const [members, setMembers] = useState({});
  const [err, setErr] = useState("");
  const [reveal, setReveal] = useState(false);

  useEffect(() => {
    providers.members().then((ms) => setMembers(Object.fromEntries(ms.map((m) => [m.id, m])))).catch(() => {});
  }, []);
  useEffect(() => {
    let alive = true;
    const load = () => hive.session(id).then((x) => alive && setS(x)).catch((e) => alive && setErr(e.message));
    load();
    const t = setInterval(() => { if (!s || s.status === "running" || s.status === "pending") load(); }, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [id, s?.status]); // eslint-disable-line

  const rounds = useMemo(() => {
    if (!s) return [];
    const by = {};
    for (const t of s.turns) (by[t.round_no] ||= []).push(t);
    return Object.entries(by).sort((a, b) => a[0] - b[0]);
  }, [s]);

  if (err) return <div className="error">{err}</div>;
  if (!s) return <div className="muted">Loading…</div>;
  const name = (t) => reveal ? (members[t.member_id]?.nickname || `${members[t.member_id]?.provider}/${members[t.member_id]?.model}` || `#${t.member_id}`) : t.alias;
  const v = s.verdict || {};

  const del = async () => { if (confirm("Delete this session and its transcript?")) { await hive.remove(s.id); nav("/"); } };

  return (
    <div>
      <div className="row between">
        <div><Link to="/" className="muted small">← Sessions</Link><h1>{s.title}</h1></div>
        <div className="row">
          <label className="chip"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Reveal models</label>
          {s.status !== "running" && <button className="danger" onClick={del}>Delete</button>}
        </div>
      </div>
      <div className="card meta">
        <span className={`pill ${s.status}`}>{s.status}</span>
        <span>{s.task_type}</span>
        <span>{usd(s.spent_usd)} of ${s.budget_usd}</span>
        <span>{s.turns.length} calls</span>
        <span className="muted small">{when(s.created_at)}{s.finished_at ? ` → ${when(s.finished_at)}` : ""}</span>
        {s.halt_reason && <div className="warn">{s.halt_reason}</div>}
      </div>

      <details className="card" open>
        <summary>Problem</summary>
        <pre className="wrap">{s.problem}</pre>
        {s.context && <><h4>Context</h4><pre className="wrap small">{s.context}</pre></>}
      </details>

      {s.verdict && (
        <div className="card verdict">
          <h2>Verdict {v.judge && <span className="muted small">by {reveal ? v.judge : "Judge"}{v.judge_excluded_own_answer ? " (own answer excluded)" : ""}</span>}</h2>
          {v.error ? <div className="warn">{v.error}</div> : (
            <>
              <div className="row"><strong>Winner:</strong> {v.winner} {reveal && v.alias_map?.[v.winner] && <span className="muted">({v.alias_map[v.winner]})</span>}</div>
              {v.scores && (
                <table className="scores"><thead><tr><th>Response</th>{Object.keys(Object.values(v.scores)[0] || {}).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                  <tbody>{Object.entries(v.scores).map(([alias, sc]) => (
                    <tr key={alias} className={alias === v.winner ? "win" : ""}><td>{alias}{reveal && v.alias_map?.[alias] ? ` · ${v.alias_map[alias]}` : ""}</td>{Object.values(sc).map((x, i) => <td key={i}>{x}</td>)}</tr>
                  ))}</tbody></table>
              )}
              {v.rationale && <><h4>Rationale</h4><p>{v.rationale}</p></>}
              {v.synthesis && <><h4>Synthesis</h4><pre className="wrap">{v.synthesis}</pre></>}
            </>
          )}
          {v.final_answers && (
            <details><summary>Final answers</summary>
              {Object.entries(v.final_answers).map(([alias, text]) => (
                <div key={alias} className="turn"><div className="turn-head">{alias}{reveal && v.alias_map?.[alias] ? ` · ${v.alias_map[alias]}` : ""}</div><pre className="wrap">{text}</pre></div>
              ))}
            </details>
          )}
        </div>
      )}

      <h2>Transcript</h2>
      {rounds.map(([rn, turns]) => (
        <section key={rn} className="round">
          <h3>Round {rn}</h3>
          {turns.map((t) => (
            <div key={t.id} className={`turn ${t.error ? "failed" : ""}`}>
              <div className="turn-head">
                <span className="pill">{PHASE_LABEL[t.phase] || t.phase}</span>
                <strong>{name(t)}</strong>
                <span className="muted small">{t.tokens_in}→{t.tokens_out} tok · {usd(t.cost_usd)} · {t.latency_ms}ms</span>
              </div>
              {t.error ? <div className="error small">{t.error}</div> : <pre className="wrap">{t.content}</pre>}
            </div>
          ))}
        </section>
      ))}
      {s.status === "running" && <div className="muted">🐝 Working… this page refreshes itself.</div>}
    </div>
  );
}

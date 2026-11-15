import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { downloadAuthed, hive, providers, usd, when } from "../api/client.js";
import { streamSession } from "../api/stream.js";
import LiveFeed from "../components/LiveFeed.jsx";
import Verdict from "../components/Verdict.jsx";

const PHASE_LABEL = { answer: "Blind answer", critique: "Critique", redteam: "Red team", revise: "Revision", judge: "Verdict" };

export default function SessionPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const [s, setS] = useState(null);
  const [members, setMembers] = useState({});
  const [err, setErr] = useState("");
  const [reveal, setReveal] = useState(false);
  const [view, setView] = useState("feed");

  useEffect(() => {
    providers.members().then((ms) => setMembers(Object.fromEntries(ms.map((m) => [m.id, m])))).catch(() => {});
  }, []);

  // Initial load, then live stream while running.
  useEffect(() => {
    let stop = null;
    hive.session(id).then((x) => {
      setS(x);
      if (x.status === "running" || x.status === "pending") {
        stop = streamSession(id, (ev, data) => {
          if (ev === "turn") {
            setS((prev) => {
              if (!prev) return prev;
              const i = prev.turns.findIndex((t) => t.id === data.id);
              const turns = i >= 0 ? prev.turns.map((t) => (t.id === data.id ? data : t)) : [...prev.turns, data];
              return { ...prev, turns };
            });
          } else if (ev === "status") {
            setS((prev) => prev && { ...prev, ...data });
          } else if (ev === "done") {
            hive.session(id).then(setS).catch(() => {});
          }
        });
      }
    }).catch((e) => setErr(e.message));
    return () => { if (stop) stop(); };
  }, [id]);

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
          <div className="seg">
            <button className={view === "feed" ? "on" : ""} onClick={() => setView("feed")}>Live feed</button>
            <button className={view === "transcript" ? "on" : ""} onClick={() => setView("transcript")}>Transcript</button>
          </div>
          <label className="chip"><input type="checkbox" checked={reveal} onChange={(e) => setReveal(e.target.checked)} /> Reveal models</label>
          <button onClick={() => downloadAuthed(hive.exportUrl(s.id, reveal), `thehive-session-${s.id}.md`).catch((e) => setErr(e.message))}>Export .md</button>
          <button onClick={() => nav("/", { state: { rerun: { title: s.title, problem: s.problem, context: s.context, task_type: s.task_type, max_rounds: s.max_rounds, budget_usd: s.budget_usd, judge_member_id: s.judge_member_id || "", member_ids: [...new Set(s.turns.map((t) => t.member_id))] } } })}>Run again</button>
          {s.status !== "running" && <button className="danger" onClick={del}>Delete</button>}
        </div>
      </div>
      <div className="card meta">
        <span className={`pill ${s.status}`}>{s.status === "running" && <span className="pulse" />}{s.status}</span>
        <span>{s.task_type}</span>
        <span>{usd(s.spent_usd)} of ${s.budget_usd}</span>
        <span>{s.turns.length} calls</span>
        <span className="muted small">{when(s.created_at)}{s.finished_at ? ` → ${when(s.finished_at)}` : ""}</span>
        {s.halt_reason && <div className="warn">{s.halt_reason}</div>}
      </div>

      <details className="card" open={s.status !== "running"}>
        <summary>Problem</summary>
        <pre className="wrap">{s.problem}</pre>
        {s.context && <><h4>Context</h4><pre className="wrap small">{s.context}</pre></>}
      </details>

      {s.verdict && !v._requested_member_ids && <Verdict v={v} reveal={reveal} />}

      {view === "feed" ? (
        <LiveFeed turns={s.turns} members={members} reveal={reveal} status={s.status} />
      ) : (
        <>
          <h2>Transcript</h2>
          {rounds.map(([rn, turns]) => (
            <section key={rn} className="round">
              <h3>Round {rn}</h3>
              {turns.map((t) => (
                <div key={t.id} className={`turn ${t.state === "error" ? "failed" : ""}`}>
                  <div className="turn-head">
                    <span className="pill">{PHASE_LABEL[t.phase] || t.phase}</span>
                    <strong>{name(t)}</strong>
                    <span className="muted small">{t.tokens_in}→{t.tokens_out} tok · {usd(t.cost_usd)} · {t.latency_ms}ms</span>
                  </div>
                  {t.state === "pending" ? <div className="muted small">thinking…</div> : t.error ? <div className="error small">{t.error}</div> : <pre className="wrap">{t.content}</pre>}
                </div>
              ))}
            </section>
          ))}
        </>
      )}
    </div>
  );
}

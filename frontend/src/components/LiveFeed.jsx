import { useEffect, useRef, useState } from "react";
import { usd } from "../api/client.js";

const PHASE = { answer: "answers", critique: "critiques", redteam: "attacks", revise: "revises", judge: "judges" };
const PALETTE = ["#f5b301", "#5ac8fa", "#ff7ab6", "#3ddc84", "#c084fc", "#ff9f43", "#64d2ff", "#f87171"];

/** Typewriter reveal for newly completed turns; instant for history. */
function Reveal({ text, animate }) {
  const [n, setN] = useState(animate ? 0 : text.length);
  useEffect(() => {
    if (!animate) { setN(text.length); return; }
    let i = 0;
    const step = Math.max(2, Math.ceil(text.length / 120));
    const t = setInterval(() => { i += step; setN(Math.min(i, text.length)); if (i >= text.length) clearInterval(t); }, 16);
    return () => clearInterval(t);
  }, [text, animate]);
  return <pre className="wrap bubble-text">{text.slice(0, n)}{n < text.length && <span className="caret">▍</span>}</pre>;
}

export default function LiveFeed({ turns, members, reveal, status }) {
  const endRef = useRef(null);
  const seenRef = useRef(new Set());
  const colorFor = (id) => PALETTE[(members[id]?.id ?? id) % PALETTE.length];
  const nameFor = (t) => reveal ? (members[t.member_id]?.nickname || `${members[t.member_id]?.provider}/${members[t.member_id]?.model}`) : t.alias;

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [turns.length, turns.map((t) => t.state).join()]);

  let lastRound = 0;
  return (
    <div className="feed">
      {turns.map((t) => {
        const roundHdr = t.round_no !== lastRound; lastRound = t.round_no;
        const isNew = t.state === "done" && !seenRef.current.has(t.id);
        if (t.state === "done") seenRef.current.add(t.id);
        const c = colorFor(t.member_id);
        return (
          <div key={t.id}>
            {roundHdr && <div className="feed-round">{t.phase === "judge" ? "Verdict" : `Round ${t.round_no}`}</div>}
            <div className={`bubble ${t.state}`} style={{ "--seat": c }}>
              <div className="bubble-head">
                <span className="dot" style={{ background: c }} />
                <strong>{nameFor(t)}</strong>
                <span className="muted small">{PHASE[t.phase] || t.phase}</span>
                {t.state === "done" && <span className="muted small right">{t.tokens_out} tok · {usd(t.cost_usd)} · {(t.latency_ms / 1000).toFixed(1)}s</span>}
              </div>
              {t.state === "pending" && <div className="thinking"><span /><span /><span /> thinking…</div>}
              {t.state === "error" && <div className="error small">{t.error}</div>}
              {t.state === "done" && <Reveal text={t.content} animate={isNew && status === "running"} />}
            </div>
          </div>
        );
      })}
      {status === "running" && turns.every((t) => t.state !== "pending") && <div className="muted small feed-wait">🐝 orchestrator preparing next phase…</div>}
      <div ref={endRef} />
    </div>
  );
}

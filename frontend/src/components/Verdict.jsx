/** Renders the judge's verdict: dissent first (that's the briefing), then scores, cited issues, synthesis. */
export default function Verdict({ v, reveal }) {
  if (!v) return null;
  const label = (alias) => (reveal && v.alias_map?.[alias] ? `${alias} · ${v.alias_map[alias]}` : alias);
  const conf = (c) => <span className={`conf-${c || "medium"} small`}>{c || "?"}</span>;

  return (
    <div className="card verdict">
      <h2>Verdict {v.judge && <span className="muted small">by {reveal ? v.judge : "Judge"}{v.judge_excluded_own_answer ? " (own answer excluded)" : ""}</span>}</h2>
      {v.error ? (
        <>
          <div className="warn">{v.error}</div>
          {v.raw && <details><summary>Raw judge output</summary><pre className="wrap small">{v.raw}</pre></details>}
        </>
      ) : (
        <>
          <div className="row"><strong>Winner:</strong> {label(v.winner)}</div>

          {Array.isArray(v.disagreements) && v.disagreements.length > 0 && (
            <>
              <h4>Where they disagree</h4>
              {v.disagreements.map((d, i) => (
                <div key={i} className="dissent">
                  <strong>{d.topic}</strong>
                  {d.positions && Object.entries(d.positions).map(([alias, pos]) => <div key={alias} className="pos small"><span className="muted">{label(alias)}:</span> {pos}</div>)}
                  <div className="call"><strong>Judge's call</strong> {conf(d.confidence)}: {d.call}</div>
                </div>
              ))}
            </>
          )}

          {v.scores && (
            <table className="scores"><thead><tr><th>Response</th>{Object.keys(Object.values(v.scores)[0] || {}).map((k) => <th key={k}>{k}</th>)}</tr></thead>
              <tbody>{Object.entries(v.scores).map(([alias, sc]) => (
                <tr key={alias} className={alias === v.winner ? "win" : ""}><td>{label(alias)}</td>{Object.values(sc).map((x, i) => <td key={i}>{x}</td>)}</tr>
              ))}</tbody></table>
          )}

          {v.per_response && (
            <details>
              <summary>Per-response findings</summary>
              {Object.entries(v.per_response).map(([alias, pr]) => (
                <div key={alias} className="per-resp">
                  <strong>{label(alias)}</strong>
                  {pr.strengths?.length > 0 && <ul>{pr.strengths.map((s, i) => <li key={i} className="small">{s}</li>)}</ul>}
                  {pr.issues?.map((it, i) => <div key={i} className="issue small"><q>{it.quote}</q> — {it.why}</div>)}
                </div>
              ))}
            </details>
          )}

          {v.rationale && <><h4>Rationale</h4><p>{v.rationale}</p></>}
          {v.synthesis && <><h4>Synthesis</h4><pre className="wrap">{v.synthesis}</pre></>}
        </>
      )}
      {v.final_answers && (
        <details><summary>Final answers</summary>
          {Object.entries(v.final_answers).map(([alias, text]) => (
            <div key={alias} className="turn"><div className="turn-head">{label(alias)}</div><pre className="wrap">{text}</pre></div>
          ))}
        </details>
      )}
    </div>
  );
}

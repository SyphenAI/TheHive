import { useState } from "react";
import { api, getToken } from "../api/client.js";

/** Three ways to add context: paste a URL, upload a text file, search the literature. Each appends a tagged block. */
export default function ContextTools({ onAppend }) {
  const [tab, setTab] = useState(null);
  const [url, setUrl] = useState("");
  const [q, setQ] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [results, setResults] = useState([]);
  const [picked, setPicked] = useState(new Set());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const run = async (fn) => { setBusy(true); setMsg(""); try { await fn(); } catch (e) { setMsg(e.message); } finally { setBusy(false); } };

  const addUrl = () => run(async () => {
    const r = await api("/api/v1/context/url", { method: "POST", body: { url } });
    onAppend(r.block); setMsg(`Added ${r.label} (${r.chars.toLocaleString()} chars)`); setUrl("");
  });
  const addFile = (e) => {
    const f = e.target.files?.[0]; if (!f) return;
    run(async () => {
      const fd = new FormData(); fd.append("file", f);
      const res = await fetch("/api/v1/context/upload", { method: "POST", headers: { authorization: `Bearer ${getToken()}` }, body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `upload ${res.status}`);
      onAppend(data.block); setMsg(`Added ${data.label} (${data.chars.toLocaleString()} chars)`);
    });
    e.target.value = "";
  };
  const search = () => run(async () => {
    const r = await api("/api/v1/context/scholar", { method: "POST", body: { query: q, limit: 10, year_from: yearFrom ? Number(yearFrom) : null } });
    setResults(r.items); setPicked(new Set(r.items.slice(0, 5).map((i) => i.n)));
    if (r.count === 0) setMsg("No results. Try broader terms.");
  });
  const addEvidence = () => run(async () => {
    const items = results.filter((i) => picked.has(i.n));
    const r = await api("/api/v1/context/scholar/block", { method: "POST", body: { query: q, items } });
    onAppend(r.block); setMsg(`Added ${r.count} source(s) as citable evidence`); setResults([]); setPicked(new Set());
  });
  const toggle = (n) => setPicked((p) => { const s = new Set(p); s.has(n) ? s.delete(n) : s.add(n); return s; });

  return (
    <div className="ctx-tools">
      <div className="row">
        <span className="muted small">Add context:</span>
        {["url", "file", "papers"].map((t) => <button key={t} type="button" className={`link small ${tab === t ? "on" : ""}`} onClick={() => setTab(tab === t ? null : t)}>{t === "papers" ? "scholarly papers" : t}</button>)}
        {busy && <span className="muted small">working…</span>}
      </div>
      {msg && <div className="small muted">{msg}</div>}
      {tab === "url" && (
        <div className="row">
          <input placeholder="https://… (public sites only; fetched server-side, HTML stripped)" value={url} onChange={(e) => setUrl(e.target.value)} />
          <button type="button" disabled={busy || !url.startsWith("http")} onClick={addUrl}>Fetch</button>
        </div>
      )}
      {tab === "file" && (
        <div className="row"><input type="file" accept=".txt,.md,.json,.yaml,.yml,.csv,.log,.py,.js,.ts,.go,.rs,.java,.c,.h,.cpp,.cs,.sh,.ps1,.toml,.xml,.html,.sql" onChange={addFile} disabled={busy} /><span className="muted small">text-like files, ≤1.5 MB</span></div>
      )}
      {tab === "papers" && (
        <div>
          <div className="row">
            <input placeholder="e.g. prompt injection defenses for LLM agents" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), search())} />
            <input className="tiny" placeholder="from year" value={yearFrom} onChange={(e) => setYearFrom(e.target.value)} />
            <button type="button" disabled={busy || q.length < 3} onClick={search}>Search</button>
          </div>
          <div className="muted small">Crossref · Semantic Scholar · OpenAlex · arXiv. Seats are told to cite results as [n].</div>
          {results.length > 0 && (
            <div className="papers">
              {results.map((it) => (
                <label key={it.n} className={`paper ${picked.has(it.n) ? "on" : ""}`}>
                  <input type="checkbox" checked={picked.has(it.n)} onChange={() => toggle(it.n)} />
                  <span><strong>{it.title}</strong> <span className="muted small">{it.author} · {it.year || "n.d."} · {it.venue || it.sources?.join("/")}{it.cited_by_count ? ` · ${it.cited_by_count} cites` : ""}</span>
                    {it.abstract && <div className="small muted abstract">{it.abstract.slice(0, 220)}{it.abstract.length > 220 ? "…" : ""}</div>}
                    {it.url && <a className="small" href={it.url} target="_blank" rel="noreferrer">open ↗</a>}
                  </span>
                </label>
              ))}
              <button type="button" disabled={busy || picked.size === 0} onClick={addEvidence}>Add {picked.size} as evidence</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

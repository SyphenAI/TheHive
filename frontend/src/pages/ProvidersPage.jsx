import { useEffect, useState } from "react";
import { providers, when } from "../api/client.js";
import { useAuth } from "../api/auth.jsx";
import LensPicker from "../components/LensPicker.jsx";
import { hive } from "../api/client.js";

export default function ProvidersPage() {
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const [reg, setReg] = useState(null);
  const [tokens, setTokens] = useState([]);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [tf, setTf] = useState({ provider: "openai", label: "default", value: "", base_url_override: "" });
  const [mf, setMf] = useState({ token_id: "", model: "", nickname: "", can_answer: true, can_critique: true, can_judge: true, can_redteam: false, max_cost_per_call_usd: 0 });
  const [live, setLive] = useState({});
  const [lenses, setLenses] = useState([]);
  const [lf, setLf] = useState({ name: "", category: "general", text: "" });

  const load = () => Promise.all([providers.registry(), providers.tokens(), hive.lenses()]).then(([r, t, l]) => { setReg(r); setTokens(t); setLenses(l); }).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(""), 4000); };
  const run = async (fn, ok) => { setErr(""); try { await fn(); await load(); if (ok) flash(ok); } catch (e) { setErr(e.message); } };

  const addToken = (e) => { e.preventDefault(); run(() => providers.createToken(tf), "Token stored (encrypted)").then(() => setTf({ ...tf, value: "" })); };
  const addMember = (e) => { e.preventDefault(); run(() => providers.createMember({ ...mf, token_id: Number(mf.token_id), max_cost_per_call_usd: Number(mf.max_cost_per_call_usd) }), "Seat added"); };
  const test = async (id) => { setErr(""); try { const r = await providers.testToken(id); flash(`${r.ok ? "✅" : "❌"} ${r.message} (${r.latency_ms}ms)`); } catch (e) { setErr(e.message); } };
  const discover = async (id) => { try { const r = await providers.models(id); setLive((l) => ({ ...l, [id]: r.live })); } catch (e) { setErr(e.message); } };
  const kill = () => { if (confirm("Wipe ALL stored provider keys? They stay live at each vendor until you revoke them there.")) run(() => providers.killSwitch(), "All keys wiped"); };

  const modelsFor = (tokenId) => {
    const t = tokens.find((x) => x.id === Number(tokenId));
    if (!t || !reg) return [];
    const fromReg = Object.keys(reg.providers[t.provider]?.models || {});
    return [...new Set([...fromReg, ...(live[t.id] || [])])];
  };

  return (
    <div>
      <div className="row between"><h1>Providers &amp; seats</h1>{admin && <button className="danger" onClick={kill}>Kill switch</button>}</div>
      {err && <div className="error">{err}</div>}
      {msg && <div className="ok">{msg}</div>}

      <div className="two-col">
        <section>
          <h2>Model keys</h2>
          <p className="muted small">Encrypted at rest with TOKEN_FERNET_KEY. The UI only ever sees a mask.</p>
          {tokens.filter((t) => !reg?.research_providers?.[t.provider]).map((t) => (
            <div key={t.id} className={`card ${t.is_active ? "" : "off"}`}>
              <div className="row between">
                <strong>{reg?.providers[t.provider]?.display || t.provider} <span className="muted">/ {t.label}</span></strong>
                <code>{t.masked_value}</code>
              </div>
              {t.base_url_override && <div className="small muted">{t.base_url_override}</div>}
              <div className="small muted">last used {when(t.last_used_at)} · {t.members.length} seat(s)</div>
              {admin && (
                <div className="row actions">
                  <button onClick={() => test(t.id)}>Test</button>
                  <button onClick={() => discover(t.id)}>Discover models</button>
                  <button onClick={() => run(() => providers.toggleToken(t.id, !t.is_active))}>{t.is_active ? "Disable" : "Enable"}</button>
                  <button className="danger" onClick={() => confirm("Delete this key and all its seats?") && run(() => providers.deleteToken(t.id), "Deleted")}>Delete</button>
                </div>
              )}
              {live[t.id] && <div className="small muted">Live: {live[t.id].slice(0, 20).join(", ")}{live[t.id].length > 20 ? "…" : ""}</div>}
            </div>
          ))}
          <h2>Research sources</h2>
          <p className="muted small">The papers tab works with no keys (Crossref, Semantic Scholar, OpenAlex, arXiv). These are optional upgrades, stored in the same vault.</p>
          {reg && Object.entries(reg.research_providers || {}).map(([k, rp]) => {
            const t = tokens.find((x) => x.provider === k);
            return (
              <div key={k} className={`card ${t && !t.is_active ? "off" : ""}`}>
                <div className="row between"><strong>{rp.display}</strong>{t ? <code>{t.masked_value}</code> : <span className="pill">not set</span>}</div>
                <div className="small muted">{rp.note} Unlocks: {rp.unlocks}. <a href={rp.signup} target="_blank" rel="noreferrer">get a key ↗</a></div>
                {admin && t && (
                  <div className="row actions">
                    <button onClick={() => test(t.id)}>Test</button>
                    <button onClick={() => run(() => providers.toggleToken(t.id, !t.is_active))}>{t.is_active ? "Disable" : "Enable"}</button>
                    <button className="danger" onClick={() => confirm("Delete this key?") && run(() => providers.deleteToken(t.id), "Deleted")}>Delete</button>
                  </div>
                )}
                {admin && !t && <div className="row actions"><button onClick={() => setTf({ ...tf, provider: k, label: "default" })}>Add key below</button></div>}
              </div>
            );
          })}
          {admin && reg && (
            <form className="card" onSubmit={addToken}>
              <h3>Add key</h3>
              <div className="row">
                <label>Provider<select value={tf.provider} onChange={(e) => setTf({ ...tf, provider: e.target.value })}>
                  <optgroup label="Models">{Object.entries(reg.providers).map(([k, p]) => <option key={k} value={k}>{p.display}</option>)}</optgroup>
                  <optgroup label="Research sources">{Object.entries(reg.research_providers || {}).map(([k, p]) => <option key={k} value={k}>{p.display}</option>)}</optgroup>
                </select></label>
                <label>Label<input value={tf.label} onChange={(e) => setTf({ ...tf, label: e.target.value })} /></label>
              </div>
              <label>API key<input type="password" required value={tf.value} onChange={(e) => setTf({ ...tf, value: e.target.value })} autoComplete="off" /></label>
              {(tf.provider === "custom" || tf.base_url_override) && <label>Base URL<input value={tf.base_url_override} onChange={(e) => setTf({ ...tf, base_url_override: e.target.value })} placeholder="https://host/v1" /></label>}
              <button>Store encrypted</button>
            </form>
          )}
        </section>

        <section>
          <h2>Seats</h2>
          <p className="muted small">A seat is one model on one key. Flags: <b>answer</b>, <b>critique</b>, <b>judge</b>, <b>redteam</b> (attacks the leading answers during the critique round; usually a seat that does not answer).</p>
          {tokens.flatMap((t) => t.members.map((m) => (
            <div key={m.id} className={`card ${m.enabled ? "" : "off"}`}>
              <div className="row between">
                <strong>{m.nickname || `${m.provider}/${m.model}`}</strong>
                <span className="muted small">{m.provider} · {m.model}</span>
              </div>
              {admin && (
                <div className="row actions">
                  {["can_answer", "can_critique", "can_judge", "can_redteam"].map((f) => (
                    <label key={f} className={`chip ${m[f] ? "on" : ""}`}><input type="checkbox" checked={m[f]} onChange={() => run(() => providers.updateMember(m.id, { [f]: !m[f] }))} />{f.replace("can_", "")}</label>
                  ))}
                  <label className="chip">cap $<input className="tiny" type="number" step="0.01" min="0" value={m.max_cost_per_call_usd} onChange={(e) => run(() => providers.updateMember(m.id, { max_cost_per_call_usd: Number(e.target.value) }))} /></label>
                  <label className="chip">lens <LensPicker compact lenses={lenses} value={{ name: m.lens_name, text: m.lens_text }} onChange={(v) => run(() => providers.updateMember(m.id, { lens_name: v.name, lens_text: v.text }))} /></label>
                  <button onClick={() => run(() => providers.updateMember(m.id, { enabled: !m.enabled }))}>{m.enabled ? "Disable" : "Enable"}</button>
                  <button className="danger" onClick={() => run(() => providers.deleteMember(m.id), "Seat removed")}>Remove</button>
                </div>
              )}
            </div>
          )))}
          {admin && tokens.length > 0 && (
            <form className="card" onSubmit={addMember}>
              <h3>Add seat</h3>
              <label>Key<select required value={mf.token_id} onChange={(e) => setMf({ ...mf, token_id: e.target.value, model: "" })}>
                <option value="">Pick a key…</option>
                {tokens.filter((t) => !reg?.research_providers?.[t.provider]).map((t) => <option key={t.id} value={t.id}>{reg?.providers[t.provider]?.display || t.provider} / {t.label}</option>)}</select></label>
              <label>Model
                <input required list="models" value={mf.model} onChange={(e) => setMf({ ...mf, model: e.target.value })} placeholder="type or pick" />
                <datalist id="models">{modelsFor(mf.token_id).map((m) => <option key={m} value={m} />)}</datalist>
              </label>
              <div className="row">
                <label>Nickname<input value={mf.nickname} onChange={(e) => setMf({ ...mf, nickname: e.target.value })} placeholder="optional" /></label>
                <label>Per-call cap (USD, 0 = none)<input type="number" step="0.01" min="0" value={mf.max_cost_per_call_usd} onChange={(e) => setMf({ ...mf, max_cost_per_call_usd: e.target.value })} /></label>
              </div>
              <div className="row">
                {["can_answer", "can_critique", "can_judge", "can_redteam"].map((f) => (
                  <label key={f} className={`chip ${mf[f] ? "on" : ""}`}><input type="checkbox" checked={mf[f]} onChange={() => setMf({ ...mf, [f]: !mf[f] })} />{f.replace("can_", "")}</label>
                ))}
              </div>
              <label>Default lens <span className="muted small">(optional; can be overridden per session)</span>
                <LensPicker lenses={lenses} value={{ name: mf.lens_name || "", text: mf.lens_text || "" }} onChange={(v) => setMf({ ...mf, lens_name: v.name, lens_text: v.text })} /></label>
              <button disabled={!mf.token_id || !mf.model}>Add seat</button>
            </form>
          )}

          <h2>Lens library</h2>
          <p className="muted small">A lens is a short standing brief: what a seat prioritizes, is suspicious of, won't let slide. Applied after the fixed rules; never applied to the judge.</p>
          {lenses.map((l) => (
            <details key={l.id} className="card lens-card">
              <summary><strong>{l.name}</strong> <span className="muted small">{l.category}{l.builtin ? " · built-in" : ""}</span></summary>
              <p className="small">{l.text}</p>
              {admin && (
                <div className="row actions">
                  <button onClick={() => setLf({ name: l.name, category: l.category, text: l.text })}>Edit</button>
                  {!l.builtin && <button className="danger" onClick={() => confirm(`Delete lens "${l.name}"?`) && run(() => hive.deleteLens(l.id), "Lens deleted")}>Delete</button>}
                </div>
              )}
            </details>
          ))}
          {admin && (
            <form className="card" onSubmit={(e) => { e.preventDefault(); run(() => hive.saveLens(lf), "Lens saved").then(() => setLf({ name: "", category: "general", text: "" })); }}>
              <h3>{lenses.some((l) => l.name === lf.name) ? `Edit "${lf.name}"` : "Add lens"}</h3>
              <div className="row">
                <label>Name<input required value={lf.name} onChange={(e) => setLf({ ...lf, name: e.target.value })} /></label>
                <label>Category<input list="lens-cats" value={lf.category} onChange={(e) => setLf({ ...lf, category: e.target.value })} /><datalist id="lens-cats">{[...new Set(lenses.map((l) => l.category))].map((c) => <option key={c} value={c} />)}</datalist></label>
              </div>
              <label>Brief<textarea rows={4} required minLength={10} value={lf.text} onChange={(e) => setLf({ ...lf, text: e.target.value })} placeholder="2–4 sentences. What does this seat prioritize, distrust, refuse to let slide?" /></label>
              <button disabled={!lf.name || lf.text.length < 10}>Save lens</button>
            </form>
          )}
        </section>
      </div>
    </div>
  );
}

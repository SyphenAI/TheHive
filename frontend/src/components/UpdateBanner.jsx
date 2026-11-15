import { useEffect, useState } from "react";
import { system } from "../api/client.js";

export default function UpdateBanner() {
  const [st, setSt] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { system.updateCheck().then(setSt).catch(() => {}); }, []);
  if (!st) return null;
  const label = st.channel === "release" ? `v${st.current_version}` : `${st.channel}@${st.current_sha || "dev"}`;
  if (!st.update_available) {
    return <div className="small muted update">TheHive {label}{st.error ? " · update check failed" : ""}</div>;
  }
  const target = st.channel === "release" ? `v${st.latest_version}` : `${st.channel}@${st.latest_sha}`;
  return (
    <div className="update avail">
      <div className="row between"><strong>Update available</strong><span className="small">{label} → {target}</span></div>
      <button className="link small" onClick={() => setOpen(!open)}>{open ? "hide" : "how to update"}</button>
      {open && (
        <div className="small">
          <p>Run in the TheHive folder (backs up <code>data/</code> first):</p>
          <pre className="wrap small">{`# Windows\n.\\scripts\\update.ps1\n# macOS / Linux\n./scripts/update.sh`}</pre>
          {st.release_url && <a href={st.release_url} target="_blank" rel="noreferrer">Release notes ↗</a>}
        </div>
      )}
    </div>
  );
}

import { getToken } from "./client.js";

/**
 * Subscribe to a session's SSE stream using fetch (EventSource can't send the auth header).
 * onEvent(name, data) is called for 'turn' | 'status' | 'done'. Returns an abort function.
 * Reconnects automatically with ?after=<last turn id> until 'done' or abort.
 */
export function streamSession(id, onEvent) {
  const ctl = new AbortController();
  let lastTurn = 0;
  let stopped = false;

  const run = async () => {
    while (!stopped) {
      try {
        const res = await fetch(`/api/v1/hive/sessions/${id}/stream?after=${lastTurn}`, {
          headers: { authorization: `Bearer ${getToken()}`, accept: "text/event-stream" },
          signal: ctl.signal,
        });
        if (res.status === 401) { window.dispatchEvent(new Event("thehive:logout")); return; }
        if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
            if (chunk.startsWith(":")) continue;
            let ev = "message", data = "";
            for (const line of chunk.split("\n")) {
              if (line.startsWith("event:")) ev = line.slice(6).trim();
              else if (line.startsWith("data:")) data += line.slice(5).trim();
            }
            let parsed = null;
            try { parsed = JSON.parse(data); } catch { parsed = data; }
            if (ev === "turn" && parsed?.id) lastTurn = Math.max(lastTurn, parsed.id);
            onEvent(ev, parsed);
            if (ev === "done") { stopped = true; return; }
          }
        }
      } catch (e) {
        if (ctl.signal.aborted) return;
      }
      if (!stopped) await new Promise((r) => setTimeout(r, 1500));
    }
  };
  run();
  return () => { stopped = true; ctl.abort(); };
}

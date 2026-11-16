const TOKEN_KEY = "thehive.token";

export function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t) {
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(status, detail) { super(detail || `HTTP ${status}`); this.status = status; }
}

export async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = { accept: "application/json" };
  const tok = getToken();
  if (tok) headers.authorization = `Bearer ${tok}`;
  if (body !== undefined) headers["content-type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (res.status === 401) { setToken(null); window.dispatchEvent(new Event("thehive:logout")); }
  if (res.status === 204) return null;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const d = data && data.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : JSON.stringify(d || data));
  }
  return raw ? res : data;
}

export const auth = {
  login: (username, password) => api("/api/v1/auth/login/json", { method: "POST", body: { username, password } }),
  me: () => api("/api/v1/auth/me"),
  logout: () => api("/api/v1/auth/logout", { method: "POST" }).catch(() => {}),
  resetPassword: (id, new_password) => api(`/api/v1/auth/users/${id}/reset-password`, { method: "POST", body: { new_password } }),
  deactivate: (id) => api(`/api/v1/auth/users/${id}/deactivate`, { method: "POST" }),
  apiKeys: () => api("/api/v1/auth/api-keys"),
  createApiKey: (b) => api("/api/v1/auth/api-keys", { method: "POST", body: b }),
  revokeApiKey: (id) => api(`/api/v1/auth/api-keys/${id}/revoke`, { method: "POST" }),
  changePassword: (current_password, new_password) =>
    api("/api/v1/auth/change-password", { method: "POST", body: { current_password, new_password } }),
  users: () => api("/api/v1/auth/users"),
  createUser: (b) => api("/api/v1/auth/users", { method: "POST", body: b }),
};

export const providers = {
  registry: () => api("/api/v1/providers/registry"),
  tokens: () => api("/api/v1/providers/tokens"),
  createToken: (b) => api("/api/v1/providers/tokens", { method: "POST", body: b }),
  toggleToken: (id, active) => api(`/api/v1/providers/tokens/${id}/toggle?active=${active}`, { method: "POST" }),
  testToken: (id) => api(`/api/v1/providers/tokens/${id}/test`, { method: "POST" }),
  models: (id) => api(`/api/v1/providers/tokens/${id}/models`),
  deleteToken: (id) => api(`/api/v1/providers/tokens/${id}`, { method: "DELETE" }),
  killSwitch: () => api("/api/v1/providers/kill-switch", { method: "POST" }),
  members: () => api("/api/v1/providers/members"),
  createMember: (b) => api("/api/v1/providers/members", { method: "POST", body: b }),
  updateMember: (id, b) => api(`/api/v1/providers/members/${id}`, { method: "PATCH", body: b }),
  deleteMember: (id) => api(`/api/v1/providers/members/${id}`, { method: "DELETE" }),
};

export const hive = {
  budget: () => api("/api/v1/hive/budget"),
  usage: (days = 30) => api(`/api/v1/hive/usage?days=${days}`),
  sessions: () => api("/api/v1/hive/sessions"),
  session: (id) => api(`/api/v1/hive/sessions/${id}`),
  create: (b) => api("/api/v1/hive/sessions", { method: "POST", body: b }),
  remove: (id) => api(`/api/v1/hive/sessions/${id}`, { method: "DELETE" }),
  estimate: (b) => api("/api/v1/hive/estimate", { method: "POST", body: b }),
  exportUrl: (id, reveal) => `/api/v1/hive/sessions/${id}/export.md?reveal=${reveal ? "true" : "false"}`,
  presets: () => api("/api/v1/hive/presets"),
  savePreset: (b) => api("/api/v1/hive/presets", { method: "POST", body: b }),
  deletePreset: (id) => api(`/api/v1/hive/presets/${id}`, { method: "DELETE" }),
  lenses: () => api("/api/v1/hive/lenses"),
  saveLens: (b) => api("/api/v1/hive/lenses", { method: "POST", body: b }),
  deleteLens: (id) => api(`/api/v1/hive/lenses/${id}`, { method: "DELETE" }),
  panels: () => api("/api/v1/hive/panels"),
  savePanel: (b) => api("/api/v1/hive/panels", { method: "POST", body: b }),
  deletePanel: (id) => api(`/api/v1/hive/panels/${id}`, { method: "DELETE" }),
};

export const system = {
  health: () => api("/api/v1/health/detail"),
  audit: (limit = 100) => api(`/api/v1/audit?limit=${limit}`),
  version: () => api("/api/v1/system/version"),
  updateCheck: (force = false) => api(`/api/v1/system/update-check?force=${force}`),
};

export async function downloadAuthed(url, filename) {
  const res = await fetch(url, { headers: { authorization: `Bearer ${getToken()}` } });
  if (!res.ok) throw new ApiError(res.status, "download failed");
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export const usd = (n) => `$${Number(n || 0).toFixed(4)}`;
export const when = (s) => (s ? new Date(s).toLocaleString() : "—");

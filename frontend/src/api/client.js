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
  login: (username, password) => api("/api/auth/login/json", { method: "POST", body: { username, password } }),
  me: () => api("/api/auth/me"),
  changePassword: (current_password, new_password) =>
    api("/api/auth/change-password", { method: "POST", body: { current_password, new_password } }),
  users: () => api("/api/auth/users"),
  createUser: (b) => api("/api/auth/users", { method: "POST", body: b }),
};

export const providers = {
  registry: () => api("/api/providers/registry"),
  tokens: () => api("/api/providers/tokens"),
  createToken: (b) => api("/api/providers/tokens", { method: "POST", body: b }),
  toggleToken: (id, active) => api(`/api/providers/tokens/${id}/toggle?active=${active}`, { method: "POST" }),
  testToken: (id) => api(`/api/providers/tokens/${id}/test`, { method: "POST" }),
  models: (id) => api(`/api/providers/tokens/${id}/models`),
  deleteToken: (id) => api(`/api/providers/tokens/${id}`, { method: "DELETE" }),
  killSwitch: () => api("/api/providers/kill-switch", { method: "POST" }),
  members: () => api("/api/providers/members"),
  createMember: (b) => api("/api/providers/members", { method: "POST", body: b }),
  updateMember: (id, b) => api(`/api/providers/members/${id}`, { method: "PATCH", body: b }),
  deleteMember: (id) => api(`/api/providers/members/${id}`, { method: "DELETE" }),
};

export const hive = {
  budget: () => api("/api/hive/budget"),
  usage: (days = 30) => api(`/api/hive/usage?days=${days}`),
  sessions: () => api("/api/hive/sessions"),
  session: (id) => api(`/api/hive/sessions/${id}`),
  create: (b) => api("/api/hive/sessions", { method: "POST", body: b }),
  remove: (id) => api(`/api/hive/sessions/${id}`, { method: "DELETE" }),
};

export const system = {
  health: () => api("/api/health/detail"),
  audit: (limit = 100) => api(`/api/audit?limit=${limit}`),
};

export const usd = (n) => `$${Number(n || 0).toFixed(4)}`;
export const when = (s) => (s ? new Date(s).toLocaleString() : "—");

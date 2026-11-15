import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { auth as authApi, getToken, setToken } from "./client.js";

const Ctx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(null); setLoading(false); return; }
    try { setUser(await authApi.me()); } catch { setUser(null); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    refresh();
    const onLogout = () => setUser(null);
    window.addEventListener("thehive:logout", onLogout);
    return () => window.removeEventListener("thehive:logout", onLogout);
  }, [refresh]);

  const login = async (username, password) => {
    const res = await authApi.login(username, password);
    setToken(res.access_token);
    await refresh();
    return res;
  };
  const logout = async () => { await authApi.logout(); setToken(null); setUser(null); };

  return <Ctx.Provider value={{ user, loading, login, logout, refresh }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

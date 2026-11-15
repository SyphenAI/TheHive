import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./api/auth.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import ChangePasswordPage from "./pages/ChangePasswordPage.jsx";
import HivePage from "./pages/HivePage.jsx";
import SessionPage from "./pages/SessionPage.jsx";
import ProvidersPage from "./pages/ProvidersPage.jsx";
import UsagePage from "./pages/UsagePage.jsx";
import AuditPage from "./pages/AuditPage.jsx";
import AccessPage from "./pages/AccessPage.jsx";
import BudgetBar from "./components/BudgetBar.jsx";
import UpdateBanner from "./components/UpdateBanner.jsx";

function Protected({ children }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="center muted">Loading…</div>;
  if (!user) return <Navigate to="/login" replace state={{ from: loc }} />;
  if (user.must_change_password && loc.pathname !== "/change-password") return <Navigate to="/change-password" replace />;
  return children;
}

function Shell({ children }) {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="hex">⬢</span> TheHive</div>
        <nav>
          <NavLink to="/" end>Sessions</NavLink>
          <NavLink to="/providers">Providers &amp; seats</NavLink>
          <NavLink to="/usage">Usage</NavLink>
          {user?.role === "admin" && <NavLink to="/access">Users &amp; API keys</NavLink>}
          {user?.role === "admin" && <NavLink to="/audit">Audit</NavLink>}
        </nav>
        <div className="spacer" />
        {user?.role === "admin" && <UpdateBanner />}
        <BudgetBar />
        <div className="whoami">
          <div>{user?.display_name || user?.username}</div>
          <div className="muted small">{user?.role}</div>
          <button className="link" onClick={logout}>Sign out</button>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/change-password" element={<Protected><ChangePasswordPage /></Protected>} />
      <Route path="/*" element={
        <Protected>
          <Shell>
            <Routes>
              <Route path="/" element={<HivePage />} />
              <Route path="/sessions/:id" element={<SessionPage />} />
              <Route path="/providers" element={<ProvidersPage />} />
              <Route path="/usage" element={<UsagePage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/access" element={<AccessPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Shell>
        </Protected>
      } />
    </Routes>
  );
}

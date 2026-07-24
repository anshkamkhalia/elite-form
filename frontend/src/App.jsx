import { useEffect, useRef, useState } from "react";
import SessionAnalysisFlow from "./flows/SessionAnalysisFlow";
import ShotComparisonFlow from "./flows/ShotComparisonFlow";
import HistoryFlow from "./flows/HistoryFlow";
import AuthScreen from "./components/AuthScreen";
import { useAuth } from "./context/AuthContext";
import {
  IconBall,
  IconFilm,
  IconCompare,
  IconHistory,
  IconUser,
  IconLogout,
} from "./components/icons";

const TABS = [
  { id: "session", label: "Session Analysis", Icon: IconFilm },
  { id: "compare", label: "Shot Comparison", Icon: IconCompare },
  { id: "history", label: "History", Icon: IconHistory },
];

function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div className="usermenu" ref={ref}>
      <button
        type="button"
        className="usermenu-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="usermenu-avatar">
          <IconUser size={15} />
        </span>
        <span className="usermenu-name">{user?.username}</span>
      </button>
      {open && (
        <div className="usermenu-pop" role="menu">
          <div className="usermenu-head">
            Signed in as
            <strong>{user?.username}</strong>
          </div>
          <button
            type="button"
            className="usermenu-item"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              logout();
            }}
          >
            <IconLogout size={15} /> Sign out
          </button>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const { user, loading } = useAuth();
  const [tab, setTab] = useState("session");

  if (loading) {
    return (
      <div className="app-loading">
        <span className="spinner" aria-hidden="true" />
        <span>Loading…</span>
      </div>
    );
  }

  if (!user) {
    return <AuthScreen />;
  }

  return (
    <>
      <div className="app">
        <header className="appbar">
          <div className="brand">
            <span className="brand-mark">
              <IconBall size={18} />
            </span>
            <span>
              <span className="brand-name">EliteForm</span>
              <br />
              <span className="brand-sub">Computer Vision Tennis Analysis</span>
            </span>
          </div>
          <UserMenu />
        </header>

        <nav className="tabnav" aria-label="Workflow">
          {TABS.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              aria-current={tab === id ? "page" : undefined}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              <Icon size={15} /> {label}
            </button>
          ))}
        </nav>

        {tab === "session" && <SessionAnalysisFlow />}
        {tab === "compare" && <ShotComparisonFlow />}
        {tab === "history" && <HistoryFlow />}
      </div>

      <footer className="statusbar">
        Analysis runs synchronously on the backend — longer clips take longer to
        process. Your results are saved to your account automatically.
      </footer>
    </>
  );
}

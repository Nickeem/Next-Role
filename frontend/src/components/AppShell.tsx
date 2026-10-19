import { useId, type ReactNode } from "react";
import { NavLink } from "react-router-dom";

import { useCandidate } from "../candidates";
import { Icon, type IconName } from "./Icons";

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Review Queue", icon: "queue", end: true },
  { to: "/decisions", label: "Decisions", icon: "decisions" },
  { to: "/activity", label: "Activity", icon: "activity" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

function navClass(base: string) {
  return ({ isActive }: { isActive: boolean }) => (isActive ? `${base} ${base}--active` : base);
}

function CandidatePicker() {
  const id = useId();
  const { candidates, selected, select } = useCandidate();
  // With one candidate there is nothing to choose and nothing to confuse.
  if (candidates.length < 2 || !selected) return null;
  return (
    <div className="field field--inline candidate-picker">
      <label htmlFor={id}>Candidate</label>
      <select id={id} value={selected.id} onChange={(e) => select(e.target.value)}>
        {candidates.map((c) => (
          <option key={c.id} value={c.id}>
            {c.full_name}
          </option>
        ))}
      </select>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="page">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="frame">
        <aside className="rail" aria-label="Primary">
          <div className="rail__logo" aria-hidden="true">
            NR
          </div>
          <nav className="rail__nav">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={navClass("rail__link")}
                aria-label={item.label}
                title={item.label}
              >
                <Icon name={item.icon} />
              </NavLink>
            ))}
          </nav>
        </aside>
        <div className="frame__body">
          <header className="topbar">
            <span className="brand">Next-Role</span>
            <nav className="tabs" aria-label="Sections">
              {NAV.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} className={navClass("tab")}>
                  {item.label}
                </NavLink>
              ))}
            </nav>
            <CandidatePicker />
          </header>
          <main id="main" className="main" tabIndex={-1}>
            {children}
          </main>
        </div>
      </div>
      <nav className="bottom-nav" aria-label="Sections">
        {NAV.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={navClass("bottom-nav__link")}>
            <Icon name={item.icon} />
            <span>{item.label.replace("Review ", "")}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

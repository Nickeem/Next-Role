import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

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

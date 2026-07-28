"use client";

import { useEffect, useState } from "react";

export function BrandMark() {
  return (
    <div className="brand-mark" aria-hidden="true">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
        <ellipse
          cx="12" cy="12" rx="9" ry="4.1"
          stroke="#FFFFFF" strokeOpacity="0.82" strokeWidth="1.4"
          transform="rotate(-28 12 12)"
        />
        <circle cx="12" cy="12" r="3.1" fill="#00A896" />
        <circle cx="20" cy="7.6" r="1.5" fill="#F4622A" />
      </svg>
    </div>
  );
}

const SunIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="5" />
    <line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" />
    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
    <line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" />
    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
  </svg>
);

const MoonIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
  </svg>
);

// Reads the theme attribute set before paint by layout.tsx, lets the user flip
// it, and persists the choice. Sun shows in light mode, moon in dark.
export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const current = (document.documentElement.getAttribute("data-theme") as "light" | "dark") || "light";
    setTheme(current);
  }, []);

  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("ffg-theme", next);
    } catch {
      /* storage unavailable — theme still applies for this session */
    }
  };

  return (
    <button
      className="theme-toggle"
      onClick={flip}
      title="Toggle dark mode"
      aria-label="Toggle dark mode"
    >
      {theme === "dark" ? <MoonIcon /> : <SunIcon />}
    </button>
  );
}

export interface NavAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

export function TopBar({
  connected,
  showSetupLink = true,
  navAction,
}: {
  connected?: boolean;
  showSetupLink?: boolean;
  navAction?: NavAction;
}) {
  return (
    <div className="topbar">
      <div className="topbar-inner">
        <a href="/" style={{ textDecoration: "none" }}>
          <div className="brand">
            <BrandMark />
            <div>
              <div className="brand-name">Nexus Placement Intelligence</div>
              <div className="brand-sub">FFG Universe</div>
            </div>
          </div>
        </a>
        <div className="topbar-spacer" />
        {/* M9.2: attribution shown in the app UI. */}
        <a
          className="topbar-attribution"
          href="mailto:macon.reman@nexusmarketing.com"
          title="Maintained by Macon Reman"
        >
          macon.reman@nexusmarketing.com
        </a>
        {navAction && (
          <button className="ghost tiny" onClick={navAction.onClick} disabled={navAction.disabled}>
            {navAction.label}
          </button>
        )}
        {connected && <span className="topbar-status">Connected</span>}
        {showSetupLink && (
          <a href="/setup"><button className="ghost tiny">Account setup</button></a>
        )}
        <ThemeToggle />
      </div>
    </div>
  );
}

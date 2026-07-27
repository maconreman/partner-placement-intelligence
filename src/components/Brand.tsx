"use client";

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
              <div className="brand-name">FFG Universe</div>
              <div className="brand-sub">Placement Intelligence</div>
            </div>
          </div>
        </a>
        <div className="topbar-spacer" />
        {navAction && (
          <button className="ghost tiny" onClick={navAction.onClick} disabled={navAction.disabled}>
            {navAction.label}
          </button>
        )}
        {connected && <span className="topbar-status">Connected</span>}
        {showSetupLink && (
          <a href="/setup"><button className="ghost tiny">Account setup</button></a>
        )}
      </div>
    </div>
  );
}

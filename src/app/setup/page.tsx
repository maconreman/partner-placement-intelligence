"use client";

import { useCallback, useEffect, useState } from "react";
import { TopBar } from "@/components/Brand";

type Status = { data: boolean; analytics: boolean; ready: boolean; error?: string };

const ACCOUNTS: { key: "data" | "analytics"; email: string; note: string }[] = [
  {
    key: "data",
    email: "data@nexusmarketing.com",
    note: "Search Console + Drive + Sheets — owns exports and the cache workbooks.",
  },
  {
    key: "analytics",
    email: "analytics@nexusmarketing.com",
    note: "Search Console only — the analytics@ properties.",
  },
];

const ERROR_COPY: Record<string, string> = {
  no_refresh_token:
    "Google did not return a refresh token. Remove the app at myaccount.google.com/permissions, then reconnect.",
  missing_code: "The authorization was cancelled or returned no code.",
  bad_state: "Unexpected account identifier returned from Google.",
  access_denied: "Consent was denied. Reconnect to continue.",
};

export default function Setup() {
  const [status, setStatus] = useState<Status | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/api/auth/status", { cache: "no-store" });
      setStatus(await r.json());
    } catch {
      setStatus({ data: false, analytics: false, ready: false, error: "Could not reach the status endpoint." });
    }
  }, []);

  useEffect(() => {
    const url = new URL(window.location.href);
    const connected = url.searchParams.get("connected");
    const error = url.searchParams.get("error");
    if (connected) setBanner({ kind: "ok", text: `Connected ${connected}@nexusmarketing.com.` });
    else if (error) setBanner({ kind: "error", text: ERROR_COPY[error] ?? `Connection failed: ${error}` });
    if (connected || error) window.history.replaceState({}, "", "/setup");
    void refresh();
  }, [refresh]);

  const connect = (account: string) => {
    window.location.href = `/api/auth/start?account=${account}`;
  };

  const connectedCount = (status?.data ? 1 : 0) + (status?.analytics ? 1 : 0);

  return (
    <>
      <TopBar connected={status?.ready} showSetupLink={false} />
      <main className="shell">
        <div className="page-head">
          <h1>Account setup</h1>
          <p className="lead">
            Connect both Google accounts once. Tokens are stored securely and refresh
            automatically — you won&apos;t need to sign in again unless access is revoked.
          </p>
        </div>

        {banner && (
          <div className={`notice ${banner.kind === "error" ? "error" : ""}`} style={banner.kind === "ok" ? okStyle : { marginBottom: 18 }}>
            {banner.text}
          </div>
        )}
        {status?.error && (
          <div className="notice error" style={{ marginBottom: 18 }}>{status.error}</div>
        )}

        <div className="panel" style={{ marginBottom: 18, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h2 style={{ marginBottom: 4 }}>Connection status</h2>
            <p className="panel-sub" style={{ margin: 0 }}>
              {connectedCount} of 2 accounts connected.
            </p>
          </div>
          <span className={`badge ${status?.ready ? "priority" : "monitor"}`} style={{ fontSize: 12, padding: "5px 12px" }}>
            {status?.ready ? "Ready" : "Action needed"}
          </span>
        </div>

        {ACCOUNTS.map(({ key, email, note }) => {
          const connected = status?.[key] ?? false;
          return (
            <div className="panel" key={key} style={{ marginBottom: 14, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                <span
                  aria-hidden="true"
                  style={{
                    width: 10, height: 10, borderRadius: "50%", flex: "none",
                    background: connected ? "var(--success)" : "var(--border-strong)",
                  }}
                />
                <div>
                  <h2 style={{ margin: 0, fontSize: 16 }}>{email}</h2>
                  <p className="panel-sub" style={{ margin: "3px 0 0" }}>{note}</p>
                </div>
              </div>
              <button className={connected ? "ghost" : "primary"} onClick={() => connect(key)}>
                {connected ? "Reconnect" : "Connect"}
              </button>
            </div>
          );
        })}

        <div className="setup-footer">
          <span className="helptext" style={{ marginTop: 0 }}>
            {status?.ready
              ? "Both accounts connected. You're ready to run."
              : "Connect both accounts to enable the tool."}
          </span>
          <a href="/">
            <button className="accent" disabled={!status?.ready}>Continue to tool →</button>
          </a>
        </div>
      </main>
    </>
  );
}

const okStyle: React.CSSProperties = {
  marginBottom: 18,
  background: "var(--teal-50)",
  color: "var(--teal-600)",
  border: "1px solid rgba(0,168,150,0.22)",
};

"use client";

import { useEffect, useRef, useState } from "react";
import { TopBar } from "@/components/Brand";

type AuthStatus = { data: boolean; analytics: boolean; ready: boolean; error?: string };
type SyncStatus = {
  bigquery: boolean;
  gsc_last_sync: string | null;
  meta_last_sync: string | null;
  error?: string;
};
type LogLine = { text: string; kind: "log" | "error" | "done" };

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  try {
    return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return iso;
  }
}

function staleness(iso: string | null): "fresh" | "stale" | "unknown" {
  if (!iso) return "unknown";
  try {
    const d = new Date(iso);
    const days = (Date.now() - d.getTime()) / 86_400_000;
    return days <= 7 ? "fresh" : "stale";
  } catch {
    return "unknown";
  }
}

// ── Status panel ──────────────────────────────────────────────────────────────

function StatusPanel({ sync, onRefresh }: { sync: SyncStatus | null; onRefresh: () => void }) {
  const rows = [
    { label: "BigQuery", value: sync ? (sync.bigquery ? "Configured" : "Not configured") : "…", ok: sync?.bigquery },
    { label: "GSC last sync", value: sync ? formatDate(sync.gsc_last_sync) : "…", ok: sync ? staleness(sync.gsc_last_sync) === "fresh" : undefined },
    { label: "Metadata last sync", value: sync ? formatDate(sync.meta_last_sync) : "…", ok: sync ? staleness(sync.meta_last_sync) !== "unknown" : undefined },
  ];

  return (
    <div className="panel" style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Warehouse status</h2>
        <button className="ghost tiny" onClick={onRefresh}>Refresh</button>
      </div>
      {sync?.error && <div className="notice error" style={{ marginBottom: 12 }}>{sync.error}</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
        {rows.map(({ label, value, ok }) => (
          <div key={label} style={{ background: "var(--surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "12px 16px" }}>
            <div style={{ fontSize: 11, fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--muted)", marginBottom: 6 }}>{label}</div>
            <div style={{ fontSize: 14, fontWeight: 600, color: ok === true ? "var(--success)" : ok === false ? "var(--danger)" : "var(--ink)" }}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Job panel ─────────────────────────────────────────────────────────────────

function JobPanel({
  title,
  description,
  endpoint,
  schedule,
  onDone,
}: {
  title: string;
  description: string;
  endpoint: string;
  schedule: string;
  onDone?: () => void;
}) {
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [finished, setFinished] = useState<"success" | "error" | null>(null);
  const consoleRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    consoleRef.current?.scrollTo(0, consoleRef.current.scrollHeight);
  }, [lines]);

  const run = async () => {
    setRunning(true);
    setFinished(null);
    setLines([]);

    try {
      const resp = await fetch(endpoint, { method: "POST" });
      if (!resp.ok || !resp.body) throw new Error(`Server returned ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          if (!part.trim()) continue;
          const ev = JSON.parse(part);
          if (ev.type === "log") {
            setLines((prev) => [...prev, { text: ev.message, kind: "log" }]);
          } else if (ev.type === "error") {
            setLines((prev) => [...prev, { text: ev.message, kind: "error" }]);
            setFinished("error");
          } else if (ev.type === "done") {
            setFinished("success");
            onDone?.();
          }
        }
      }
    } catch (e) {
      setLines((prev) => [...prev, { text: String(e), kind: "error" }]);
      setFinished("error");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="panel" style={{ marginBottom: 18 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, marginBottom: 14 }}>
        <div>
          <h2 style={{ margin: "0 0 4px" }}>{title}</h2>
          <p className="panel-sub" style={{ margin: 0 }}>{description}</p>
        </div>
        <button
          className={running ? "ghost" : "accent"}
          style={{ whiteSpace: "nowrap", flexShrink: 0 }}
          onClick={run}
          disabled={running}
        >
          {running ? "Running…" : "Run now"}
        </button>
      </div>

      <div style={{ fontSize: 12, fontFamily: "var(--mono)", color: "var(--muted)", marginBottom: lines.length ? 10 : 0 }}>
        Schedule: {schedule}
      </div>

      {lines.length > 0 && (
        <div
          ref={consoleRef}
          className="console"
          style={{ marginTop: 10, maxHeight: 260 }}
        >
          {lines.map((l, i) => (
            <div
              key={i}
              style={{
                color: l.kind === "error"
                  ? "var(--priority)"
                  : l.kind === "done"
                  ? "var(--teal)"
                  : "var(--console-ink)",
              }}
            >
              {l.text}
            </div>
          ))}
          {finished === "success" && (
            <div style={{ color: "var(--teal)", marginTop: 6, fontWeight: 600 }}>✓ Job complete.</div>
          )}
        </div>
      )}

      {finished === "error" && !running && (
        <div className="notice error" style={{ marginTop: 10 }}>Job finished with errors, check the log above</div>
      )}
    </div>
  );
}

// ── Setup banner ──────────────────────────────────────────────────────────────

function SetupBanner() {
  return (
    <div className="panel" style={{ marginBottom: 18 }}>
      <h2>Account setup required</h2>
      <p className="panel-sub">
        Both Google accounts need to be connected before syncing. This is a one-time step.
      </p>
      <div className="row">
        <a href="/setup"><button className="primary">Go to setup →</button></a>
      </div>
    </div>
  );
}

// ── Account setup page (reused from main app) ─────────────────────────────────

function SetupPage() {
  const ACCOUNTS = [
    { key: "data" as const, email: "data@nexusmarketing.com", note: "Search Console and Drive, owns exports" },
    { key: "analytics" as const, email: "analytics@nexusmarketing.com", note: "Search Console only, analytics@ properties" },
  ];

  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [banner, setBanner] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  useEffect(() => {
    const url = new URL(window.location.href);
    const connected = url.searchParams.get("connected");
    const error = url.searchParams.get("error");
    if (connected) setBanner({ kind: "ok", text: `Connected ${connected}@nexusmarketing.com.` });
    else if (error) setBanner({ kind: "error", text: `Connection failed: ${error}` });
    if (connected || error) window.history.replaceState({}, "", "/setup");
    fetch("/api/auth/status", { cache: "no-store" }).then((r) => r.json()).then(setStatus).catch(() => {});
  }, []);

  const connectedCount = (status?.data ? 1 : 0) + (status?.analytics ? 1 : 0);

  return (
    <>
      <TopBar connected={status?.ready} showSetupLink={false} />
      <main className="shell">
        <div className="page-head">
          <h1>Account setup</h1>
          <p className="lead">Connect both Google accounts once. Tokens refresh automatically.</p>
        </div>
        {banner && (
          <div className={`notice ${banner.kind === "error" ? "error" : "ok"}`} style={{ marginBottom: 18 }}>
            {banner.text}
          </div>
        )}
        <div className="panel" style={{ marginBottom: 18, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h2 style={{ marginBottom: 4 }}>Connection status</h2>
            <p className="panel-sub" style={{ margin: 0 }}>{connectedCount} of 2 accounts connected.</p>
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
                <span style={{ width: 10, height: 10, borderRadius: "50%", flex: "none", background: connected ? "var(--success)" : "var(--border-strong)" }} />
                <div>
                  <h2 style={{ margin: 0, fontSize: 16 }}>{email}</h2>
                  <p className="panel-sub" style={{ margin: "3px 0 0" }}>{note}</p>
                </div>
              </div>
              <button className={connected ? "ghost" : "primary"} onClick={() => { window.location.href = `/api/auth/start?account=${key}`; }}>
                {connected ? "Reconnect" : "Connect"}
              </button>
            </div>
          );
        })}
        <div className="setup-footer">
          <span className="helptext" style={{ marginTop: 0 }}>
            {status?.ready ? "Both accounts connected. Return to the dashboard." : "Connect both accounts to enable sync."}
          </span>
          <a href="/"><button className="accent" disabled={!status?.ready}>← Dashboard</button></a>
        </div>
      </main>
    </>
  );
}

// ── Main dashboard ────────────────────────────────────────────────────────────

export default function AdminDashboard() {
  const [authReady, setAuthReady] = useState<boolean | null>(null);
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null);
  const [isSetupPage, setIsSetupPage] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined" && window.location.pathname.startsWith("/setup")) {
      setIsSetupPage(true);
      return;
    }
    fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => setAuthReady(Boolean(s.ready)))
      .catch(() => setAuthReady(false));
  }, []);

  const loadSyncStatus = () => {
    fetch("/api/admin/status")
      .then((r) => r.json())
      .then(setSyncStatus)
      .catch(() => {});
  };

  useEffect(() => {
    if (authReady) loadSyncStatus();
  }, [authReady]);

  if (isSetupPage) return <SetupPage />;

  if (authReady === false) {
    return (
      <>
        <TopBar connected={false} />
        <div className="shell">
          <div className="page-head">
            <h1>Admin · Placement Intelligence</h1>
            <p className="lead">Manage GSC data sync and metadata crawl jobs for the placement intelligence tool.</p>
          </div>
          <SetupBanner />
        </div>
      </>
    );
  }

  if (authReady === null) {
    return (
      <>
        <TopBar />
        <div className="shell">
          <div style={{ color: "var(--muted)", fontFamily: "var(--mono)", fontSize: 13, marginTop: 40 }}>Loading…</div>
        </div>
      </>
    );
  }

  return (
    <>
      <TopBar connected={true} showSetupLink={true} />
      <div className="shell">
        <div className="page-head">
          <h1>Admin · Placement Intelligence</h1>
          <p className="lead">
            Manage data pipeline jobs. Run GSC sync first, then metadata crawl. The main app reads
            exclusively from BigQuery, populate the warehouse before deployment
          </p>
        </div>

        <StatusPanel sync={syncStatus} onRefresh={loadSyncStatus} />

        <JobPanel
          title="Sync GSC data"
          description="Fetches all Search Console data for configured domains and writes to BigQuery. Run weekly."
          endpoint="/api/admin/sync-gsc"
          schedule="Weekly on Monday, or run manually before a new deployment"
          onDone={loadSyncStatus}
        />

        <JobPanel
          title="Sync page metadata"
          description="Crawls all pages from the GSC warehouse and writes metadata (title, H1, H2, description) to BigQuery. Run quarterly or after a content refresh."
          endpoint="/api/admin/sync-metadata"
          schedule="Quarterly at 90-day staleness, or run manually"
          onDone={loadSyncStatus}
        />

        <div className="panel">
          <h2>Support contacts</h2>
          <p className="panel-sub" style={{ margin: 0 }}>
            Macon Reman (tech lead) · Maggie Chagoya (manager) · <code>maggie.chagoya@nexusmarketing.com</code>
          </p>
        </div>
      </div>
    </>
  );
}

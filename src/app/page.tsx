"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TopBar, NavAction } from "@/components/Brand";

type Domain = { siteUrl: string; short: string; isFfg: boolean };
type StageId = "fetch" | "pages" | "seo" | "metadata" | "match" | "refine" | "rank";
type StageStatus = "idle" | "active" | "done";
type PreviewRow = {
  rank: number; page: string; matched_on: string; anchor_text: string; anchor_source?: string;
  topical_relevance_score: number; seo_score: number; page_category: string;
  tier_label: string; query: string;
  meta_title?: string; meta_description?: string; h1?: string; h2?: string;
  clicks?: number; impressions?: number; position?: number;
};
type ExportRow = Record<string, string | number>;

const STAGES: { id: StageId; label: string }[] = [
  { id: "fetch", label: "Fetch" },
  { id: "pages", label: "Pages" },
  { id: "seo", label: "SEO" },
  { id: "metadata", label: "Metadata" },
  { id: "match", label: "Match" },
  { id: "refine", label: "Relevance" },
  { id: "rank", label: "Rank" },
];

const WIZARD_STEPS = ["Domains", "Topic & dates", "Results"];
const GSC_LAG_DAYS = 3;
const PREVIEW_COLLAPSED = 5;

// Feedback verticals — inlined (not imported from server config to keep the
// client bundle free of server-only constants like HF tokens / hard negatives).
const FEEDBACK_VERTICALS = [
  "Nonprofit", "Healthcare", "Education", "Association", "Faith", "Community", "Others",
];

function presetDates(): { start: string; end: string } {
  const end = new Date();
  end.setDate(end.getDate() - GSC_LAG_DAYS);
  const start = new Date(end);
  start.setFullYear(start.getFullYear() - 1);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { start: iso(start), end: iso(end) };
}

function scoreClass(v: number): string {
  if (v >= 7) return "s-high";
  if (v >= 4) return "s-mid";
  return "s-low";
}

// page_short — last two path segments in monospace, matching the Colab cell.
function shortPath(u: string): string {
  try {
    const x = new URL(u);
    const segs = x.pathname.split("/").filter(Boolean);
    const last2 = segs.slice(-2).join("/");
    return last2 ? `/${last2}/` : "/";
  } catch {
    return u;
  }
}

// SEMrush/Moz-style fill bar — same number, more scannable. Color encodes tier.
function ScoreBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, (value / 10) * 100));
  return (
    <div className="scorebar" title={`${value.toFixed(1)} / 10`}>
      <div className="scorebar-track">
        <div className={`scorebar-fill ${scoreClass(value)}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="scorebar-val">{value.toFixed(1)}</span>
    </div>
  );
}

function Stepper({ step }: { step: number }) {
  return (
    <div className="steps">
      {WIZARD_STEPS.map((label, i) => {
        const n = i + 1;
        const cls = step === n ? "active" : step > n ? "done" : "";
        return (
          <span key={label} style={{ display: "contents" }}>
            <span className={`step-pip ${cls}`} data-n={n}>{label}</span>
            {i < WIZARD_STEPS.length - 1 && (
              <span className={`step-connector ${step > n ? "filled" : ""}`} />
            )}
          </span>
        );
      })}
    </div>
  );
}

// ── Improve future results (Colab Cell 5 · Step 4) ──────────────────────────
// Feedback is persisted to BigQuery via /api/feedback. Topic + selected domains
// travel with each submission for context.
function FeedbackPanel({ topic, domains }: { topic: string; domains: string[] }) {
  const [query, setQuery] = useState("");
  const [vertical, setVertical] = useState("");
  const [other, setOther] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [err, setErr] = useState("");

  const reset = () => {
    setQuery(""); setVertical(""); setOther(""); setSubmitted(false); setErr("");
  };

  const submit = async () => {
    setSubmitting(true);
    setErr("");
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: query.trim(),
          vertical,
          category: vertical === "Others" ? other.trim() : "",
          topic: topic.trim(),
          domains: domains.join(", "),
        }),
      });
      setSubmitted(true);
    } catch {
      setErr("Couldn't save — please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <>
        <div className="notice ok">Thanks — your feedback was saved and will help tune future relevance scoring.</div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="tiny" onClick={reset}>Submit another</button>
        </div>
      </>
    );
  }

  return (
    <>
      <label className="field">
        <span>Query (paste a search term to flag)</span>
        <input type="text" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. donor management platform" />
      </label>
      <label className="field">
        <span>Vertical</span>
        <select value={vertical} onChange={(e) => setVertical(e.target.value)}>
          <option value="">Select a vertical…</option>
          {FEEDBACK_VERTICALS.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      </label>
      {vertical === "Others" && (
        <label className="field">
          <span>Specify category</span>
          <input type="text" value={other} onChange={(e) => setOther(e.target.value)} placeholder="Name the vertical" />
        </label>
      )}
      {err && <div className="notice error" style={{ marginBottom: 12 }}>{err}</div>}
      <div className="row">
        <button
          className="primary"
          onClick={submit}
          disabled={submitting || !query.trim() || !vertical || (vertical === "Others" && !other.trim())}
        >
          {submitting ? "Saving…" : "Submit feedback"}
        </button>
        <button className="ghost" onClick={reset} disabled={submitting}>Skip</button>
      </div>
    </>
  );
}

export default function Wizard() {
  const [step, setStep] = useState(1);

  // Step 1
  const [domains, setDomains] = useState<Domain[]>([]);
  const [domainErr, setDomainErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Step 2
  const [clientName, setClientName] = useState("");
  const [topic, setTopic] = useState("");
  const init = useMemo(presetDates, []);
  const [startDate, setStartDate] = useState(init.start);
  const [endDate, setEndDate] = useState(init.end);
  const [formErr, setFormErr] = useState("");

  // Step 3
  const [stages, setStages] = useState<Record<StageId, StageStatus>>({
    fetch: "idle", pages: "idle", seo: "idle", metadata: "idle", match: "idle", refine: "idle", rank: "idle",
  });
  const [logLines, setLogLines] = useState<string[]>([]);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [funnel, setFunnel] = useState<{ rowsFetched: number; pages: number; matched: number; scored: number } | null>(null);
  const [preview, setPreview] = useState<PreviewRow[]>([]);
  const [exportRows, setExportRows] = useState<ExportRow[]>([]);
  const [runError, setRunError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // Elapsed seconds since goRun() fired. Powers the visible "Running for Xm Ys"
  // counter and the 90s auto-expand of the Fetch details log.
  const [elapsedSec, setElapsedSec] = useState(0);
  const consoleRef = useRef<HTMLDivElement>(null);

  // Results view state
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  // Export
  const [exporting, setExporting] = useState(false);
  const [exportUrl, setExportUrl] = useState("");
  const [filename, setFilename] = useState("");
  const [downloading, setDownloading] = useState(false);

  const doDownload = async () => {
    setDownloading(true);
    try {
      const resp = await fetch("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows: exportRows, filename }),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(j.error || `Download failed (${resp.status}).`);
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename.match(/\.xlsx$/i) ? filename : `${filename || "FFG-Placements"}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setDownloading(false);
    }
  };

  const [authReady, setAuthReady] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((s) => {
        const ready = Boolean(s.ready);
        setAuthReady(ready);
        if (ready) {
          fetch("/api/domains")
            .then((r) => r.json())
            .then((d) => {
              if (d.error) setDomainErr(d.error);
              else setDomains(d.domains ?? []);
            })
            .catch((e) => setDomainErr(String(e)));
        }
      })
      .catch(() => setAuthReady(false));
  }, []);

  useEffect(() => {
    consoleRef.current?.scrollTo(0, consoleRef.current.scrollHeight);
  }, [logLines]);

  // Live elapsed-time counter. Increments every second while a run is in flight
  // so the user always sees something moving on the screen, even when the
  // pipeline is silent (e.g. mid-embedding). Reset to 0 on each new run.
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  // After 90 seconds of runtime, auto-open the Fetch details log so the user
  // can see activity continuing — removes any impression the app is frozen.
  // Also auto-opens when Stage 6 (Relevance) starts, since that's the slowest
  // stage where users are most likely to suspect a hang.
  useEffect(() => {
    if (!running) return;
    if (elapsedSec === 90) setConsoleOpen(true);
  }, [elapsedSec, running]);
  useEffect(() => {
    if (running && stages.refine === "active") setConsoleOpen(true);
  }, [running, stages.refine]);

  const toggle = (siteUrl: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(siteUrl) ? next.delete(siteUrl) : next.add(siteUrl);
      return next;
    });

  const pick = (which: "ffg" | "clients" | "all" | "clear") => {
    if (which === "clear") return setSelected(new Set());
    if (which === "all") return setSelected(new Set(domains.map((d) => d.siteUrl)));
    setSelected(new Set(domains.filter((d) => (which === "ffg" ? d.isFfg : !d.isFfg)).map((d) => d.siteUrl)));
  };

  const applyPreset = () => {
    const p = presetDates();
    setStartDate(p.start);
    setEndDate(p.end);
  };

  const toggleRow = (rank: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(rank) ? next.delete(rank) : next.add(rank);
      return next;
    });

  const goRun = async () => {
    if (!topic.trim()) return setFormErr("Enter a topic before running.");
    setFormErr("");
    setStep(3);
    setRunError(null);
    setExportUrl("");
    setPreview([]);
    setExportRows([]);
    setFunnel(null);
    setLogLines([]);
    setShowAll(false);
    setExpanded(new Set());
    setStages({ fetch: "idle", pages: "idle", seo: "idle", metadata: "idle", match: "idle", refine: "idle", rank: "idle" });
    setElapsedSec(0);
    setRunning(true);

    const slug = (s: string) => s.replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    setFilename(`${slug(clientName || "Client")}-Placements-${slug(topic)}.xlsx`);

    try {
      const resp = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domains: [...selected], topic: topic.trim(), startDate, endDate }),
      });
      if (!resp.ok || !resp.body) {
        const j = await resp.json().catch(() => ({}));
        throw new Error(j.error || `Run failed (${resp.status}).`);
      }
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
          handleEvent(JSON.parse(part));
        }
      }
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  function handleEvent(e: any) {
    if (e.type === "stage") setStages((prev) => ({ ...prev, [e.stage]: e.status }));
    else if (e.type === "log") setLogLines((prev) => [...prev, e.message]);
    else if (e.type === "funnel") setFunnel(e);
    else if (e.type === "error") setRunError(e.message);
    else if (e.type === "result") {
      setExportRows(e.rows);
      setPreview(e.preview);
    }
  }

  const doExport = async () => {
    setExporting(true);
    setExportUrl("");
    try {
      const resp = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows: exportRows, filename }),
      });
      const j = await resp.json();
      if (j.error) throw new Error(j.error);
      setExportUrl(j.url);
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  // Topbar navigation — Back / New run live in the persistent chrome (top right).
  const nav: NavAction | undefined =
    step === 2 ? { label: "← Back to domains", onClick: () => setStep(1) }
    : step === 3 ? { label: "← New run", onClick: () => setStep(2), disabled: running }
    : undefined;

  if (authReady === false) {
    return (
      <>
        <TopBar connected={false} />
        <div className="shell">
          <div className="page-head">
            <h1>Connect your Google accounts</h1>
            <p className="lead">
              FFG Universe reads Search Console data across the network. Both the
              data@ and analytics@ accounts need to be connected before you can run
              a placement analysis — a one-time step.
            </p>
          </div>
          <div className="panel">
            <h2>Account access required</h2>
            <p className="panel-sub">
              Tokens are stored securely and refresh automatically. You won&apos;t be
              asked to sign in again unless access is revoked.
            </p>
            <div className="row">
              <a href="/setup"><button className="primary">Go to setup →</button></a>
            </div>
          </div>
        </div>
      </>
    );
  }

  // Preview collapse — all results visible, 5-row default.
  const visible = showAll ? preview : preview.slice(0, PREVIEW_COLLAPSED);

  return (
    <>
      <TopBar connected={authReady === true} navAction={nav} />
      <div className="shell">
        <div className="page-head">
          <h1>Placement Intelligence</h1>
          <p className="lead">
            Discover high-value content placement opportunities across the FFG
            network and client sites — ranked by topical relevance and SEO strength.
          </p>
        </div>

        <Stepper step={step} />

        {step === 1 && (
          <div className="panel">
            <h2>Select domains</h2>
            <p className="panel-sub">Choose the properties to analyze. FFG-owned and client domains are tagged.</p>
            {domainErr && <div className="notice error" style={{ marginBottom: 16 }}>{domainErr}</div>}
            <div className="row" style={{ marginBottom: 16 }}>
              <button className="tiny" onClick={() => pick("ffg")}>FFG only</button>
              <button className="tiny" onClick={() => pick("clients")}>Clients only</button>
              <button className="tiny" onClick={() => pick("all")}>Select all</button>
              <button className="tiny ghost" onClick={() => pick("clear")}>Clear</button>
              <div className="spacer" />
              <span style={{ color: "var(--muted)", fontSize: 12.5, fontWeight: 500 }}>{selected.size} of {domains.length} selected</span>
            </div>
            <div className="domain-grid">
              {domains.map((d) => (
                <label key={d.siteUrl} className={`domain ${selected.has(d.siteUrl) ? "sel" : ""}`}>
                  <input type="checkbox" checked={selected.has(d.siteUrl)} onChange={() => toggle(d.siteUrl)} />
                  {d.short}
                  <span className="tag">{d.isFfg ? "FFG" : "client"}</span>
                </label>
              ))}
              {!domains.length && !domainErr && <span style={{ color: "var(--muted)" }}>Loading properties…</span>}
            </div>
            <div className="row end" style={{ marginTop: 22 }}>
              <button className="primary" disabled={!selected.size} onClick={() => { setDomainErr(""); setStep(2); }}>
                Continue →
              </button>
            </div>
            {!selected.size && <p className="helptext">Select at least one domain to continue.</p>}
          </div>
        )}

        {step === 2 && (
          <div className="panel">
            <h2>Topic &amp; date range</h2>
            <p className="panel-sub">Describe what you&apos;re placing. The engine matches it against each page&apos;s search demand and content.</p>
            <label className="field">
              <span>Client name (optional — used in the export filename)</span>
              <input type="text" value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="e.g. Double the Donation" />
            </label>
            <label className="field">
              <span>Topic</span>
              <textarea rows={2} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. nonprofit CRM software" />
            </label>

            <label className="field" style={{ marginBottom: 8 }}>
              <span>Date range</span>
            </label>
            <div className="row" style={{ marginBottom: 4 }}>
              <button className="tiny" onClick={applyPreset}>Last 12 months</button>
              <span className="range-label">{startDate} → {endDate}</span>
            </div>
            <details className="disclosure">
              <summary>Custom range</summary>
              <div className="disclosure-body">
                <div className="row">
                  <label className="field" style={{ flex: 1, marginBottom: 0 }}>
                    <span>Start date</span>
                    <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                  </label>
                  <label className="field" style={{ flex: 1, marginBottom: 0 }}>
                    <span>End date</span>
                    <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                  </label>
                </div>
              </div>
            </details>

            {formErr && <div className="notice error" style={{ marginTop: 16 }}>{formErr}</div>}
            <div className="row end" style={{ marginTop: 18 }}>
              <button className="accent" onClick={goRun}>Run analysis →</button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="panel">
            {/* ── Progress ─────────────────────────────────────────────── */}
            <div className="section-rule"><span>Progress</span></div>

            {running && (() => {
              const idx = STAGES.findIndex((s) => stages[s.id] === "active");
              const cur = idx >= 0 ? idx : STAGES.filter((s) => stages[s.id] === "done").length;
              const label = STAGES[Math.min(cur, STAGES.length - 1)]?.label ?? "";
              const mm = Math.floor(elapsedSec / 60);
              const ss = elapsedSec % 60;
              const elapsedStr = mm > 0 ? `${mm}m ${ss}s` : `${ss}s`;
              return (
                <p className="run-status">
                  <strong>Running</strong> — Stage {Math.min(cur + 1, STAGES.length)} of {STAGES.length} — {label}
                  <span className="run-elapsed">{elapsedStr}</span>
                </p>
              );
            })()}

            <div className="tracker">
              {STAGES.map((s) => (
                <div key={s.id} className={`stage ${stages[s.id]}`}>
                  <div className="label">{s.label}</div>
                </div>
              ))}
            </div>

            {/* Indeterminate progress bar — animates continuously while a run is
                in flight so users see motion even between log lines. Hidden once
                the run completes; the funnel line below takes over. */}
            {running && (
              <div className="progress-wrap" aria-hidden="true">
                <div className="progress-bar" />
              </div>
            )}

            {/* Stage 6 (Relevance) is the longest stage — explicit patience note
                so users do not assume the app has frozen. Disappears once Stage 6
                completes and the rank stage begins. */}
            {running && stages.refine === "active" && (
              <div className="patience-note">
                AI scoring in progress — this is the slowest stage and can take
                3–5 minutes on a large domain set. The app is still running.
              </div>
            )}

            {funnel && (
              <div className="funnel">
                <b>{funnel.rowsFetched.toLocaleString()}</b> GSC rows → <b>{funnel.pages.toLocaleString()}</b> unique pages →{" "}
                <b>{funnel.matched.toLocaleString()}</b> quick-matched → <b>{funnel.scored.toLocaleString()}</b> scored
              </div>
            )}

            <details
              className="disclosure"
              open={consoleOpen}
              onToggle={(e) => setConsoleOpen((e.currentTarget as HTMLDetailsElement).open)}
            >
              <summary>Fetch details</summary>
              <div className="disclosure-body">
                <div className="console" ref={consoleRef}>
                  {logLines.map((l, i) => <div className="line" key={i}>{l}</div>)}
                  {!logLines.length && <div className="line">Starting pipeline…</div>}
                </div>
              </div>
            </details>

            {runError && (
              <div className="notice error" style={{ marginTop: 16 }}>
                {runError}
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="tiny" onClick={goRun} disabled={running}>Try again</button>
                  <button className="tiny ghost" onClick={() => setStep(2)}>← Change topic</button>
                </div>
              </div>
            )}

            {preview.length > 0 && (
              <>
                {/* ── Results ──────────────────────────────────────────── */}
                <div className="section-rule"><span>Results</span></div>

                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th className="th-x" />
                        <th title="Ranked placement order (composite of relevance and SEO)">#</th>
                        <th title="The page URL — last two path segments shown; full URL in the row drawer">Page</th>
                        <th title="Which on-page surface qualified the page. Hover the tag for the matching anchor phrase">Matched on</th>
                        <th title="How closely this page's content matches your topic (0–10)">Relevance</th>
                        <th title="Search-performance strength from GSC clicks, impressions, and position (0–10)">SEO</th>
                        <th title="Page type, auto-classified from URL and content">Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((r) => (
                        <RowWithDrawer key={r.rank} r={r} open={expanded.has(r.rank)} onToggle={() => toggleRow(r.rank)} />
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="results-meta">
                  <span className="helptext" style={{ margin: 0 }}>
                    Showing {visible.length} of {preview.length} placements
                  </span>
                  {preview.length > PREVIEW_COLLAPSED && (
                    <button className="ghost tiny" onClick={() => setShowAll((v) => !v)}>
                      {showAll ? "Show fewer" : `Show all ${preview.length}`}
                    </button>
                  )}
                </div>

                {/* ── Improve future results (collapsible) ──────────────── */}
                <div className="section-rule"><span>Improve future results</span></div>
                <details className="disclosure">
                  <summary>Flag a result to tune scoring</summary>
                  <div className="disclosure-body">
                    <p className="feedback-note">
                      Submitted feedback will not affect the current run. It will
                      be reviewed by the development team and used to improve
                      scoring in future versions.
                    </p>
                    <FeedbackPanel topic={topic} domains={[...selected]} />
                  </div>
                </details>

                {/* ── Sticky export bar ─────────────────────────────────── */}
                <div className="export-bar">
                  <span className="export-count"><b>{exportRows.length.toLocaleString()}</b> placements found</span>
                  <div className="spacer" />
                  <input
                    className="export-filename"
                    type="text"
                    value={filename}
                    onChange={(e) => setFilename(e.target.value)}
                    aria-label="Export filename"
                  />
                  <button className="accent" onClick={doExport} disabled={exporting}>
                    {exporting ? "Exporting…" : "Export to Drive ↗"}
                  </button>
                  <button className="ghost tiny" onClick={doDownload} disabled={downloading}>
                    {downloading ? "Downloading…" : "↓ Download"}
                  </button>
                  {exportUrl && (
                    <a className="export-link" href={exportUrl} target="_blank" rel="noreferrer">✓ Open</a>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}

// Table row + expandable detail drawer (Moz "why did this rank?" pattern).
function RowWithDrawer({ r, open, onToggle }: { r: PreviewRow; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className={open ? "row-open" : ""} onClick={onToggle} style={{ cursor: "pointer" }}>
        <td className="td-x"><span className={`row-caret ${open ? "open" : ""}`}>▸</span></td>
        <td><span className="rank-chip">{r.rank}</span></td>
        <td className="page-cell">
          <a className="page-short" href={r.page} target="_blank" rel="noreferrer" title={r.page} onClick={(e) => e.stopPropagation()}>
            {shortPath(r.page)}
          </a>
        </td>
        <td>
          {r.matched_on
            ? <span className="matched-tag" title={r.anchor_text ? `Anchor: ${r.anchor_text}` : "No anchor phrase"}>{r.matched_on}</span>
            : <span style={{ color: "var(--faint)" }}>—</span>}
        </td>
        <td><ScoreBar value={r.topical_relevance_score} /></td>
        <td><ScoreBar value={r.seo_score} /></td>
        <td>{r.page_category && r.page_category !== "Other" ? <span className="cat">{r.page_category}</span> : null}</td>
      </tr>
      {open && (
        <tr className="drawer-row">
          <td />
          <td colSpan={6}>
            <div className="drawer">
              <div className="drawer-field"><span>Full URL</span><a href={r.page} target="_blank" rel="noreferrer">{r.page}</a></div>
              <div className="drawer-field"><span>Top query</span>{r.query || "—"}</div>

              {r.meta_title && <div className="drawer-field"><span>Title tag</span>{r.meta_title}</div>}
              {r.h1 && <div className="drawer-field"><span>H1</span>{r.h1}</div>}
              {(r.clicks != null || r.impressions != null) && (
                <div className="drawer-field">
                  <span>GSC</span>
                  {(r.clicks ?? 0).toLocaleString()} clicks · {(r.impressions ?? 0).toLocaleString()} impressions
                  {r.position != null ? ` · pos ${r.position.toFixed(1)}` : ""}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

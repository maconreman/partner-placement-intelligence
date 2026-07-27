"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { TopBar, NavAction } from "@/components/Brand";

type Domain = { siteUrl: string; short: string; isFfg: boolean; vertical?: string };
type StageId = "fetch" | "pages" | "seo" | "metadata" | "match" | "refine" | "rank";
type StageStatus = "idle" | "active" | "done";
type ContentType = "Listicle" | "How-to" | "Comparison" | null;
type PreviewRow = {
  rank: number; page: string; matched_on: string; anchor_text: string; anchor_source?: string;
  topical_relevance_score: number; seo_score: number; page_category: string;
  content_type?: ContentType;
  tier_label: string; query: string;
  meta_title?: string; meta_description?: string; h1?: string;
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

// Preferred display order for vertical filter chips.
// Only verticals that are present in the loaded domain list appear.
const VERTICAL_ORDER = ["FFG", "Nonprofit", "Education", "Association", "Healthcare", "Community", "Faith", "Other"];

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

function anchorTone(src?: string): "ok" | "warn" | "weak" {
  if (src === "h1" || src === "h2") return "ok";
  if (src === "query_all") return "warn";
  return "weak";
}
const ANCHOR_NOTE: Record<string, string> = {
  ok: "verbatim page heading, ready to use",
  warn: "from a search query, reword before use",
  weak: "from URL slug or meta, reword before use",
};

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

// M3.5: Content-type badge component
function ContentTypeBadge({ type }: { type?: ContentType }) {
  if (!type) return null;
  const cls = type === "Listicle" ? "listicle" : type === "How-to" ? "howto" : "comparison";
  return <span className={`ct-badge ${cls}`}>{type}</span>;
}

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

function FeedbackPanel({ topic, domains }: { topic: string; domains: string[] }) {
  const [query, setQuery] = useState("");
  const [vertical, setVertical] = useState("");
  const [other, setOther] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [err, setErr] = useState("");

  const reset = () => { setQuery(""); setVertical(""); setOther(""); setSubmitted(false); setErr(""); };

  const submit = async () => {
    setSubmitting(true); setErr("");
    try {
      await fetch("/api/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: query.trim(), vertical, category: vertical === "Others" ? other.trim() : "", topic: topic.trim(), domains: domains.join(", ") }),
      });
      setSubmitted(true);
    } catch {
      setErr("Couldn't save, try again");
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <>
        <div className="notice ok">Thanks, your feedback was saved and will help tune future relevance scoring</div>
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
        <button className="primary" onClick={submit} disabled={submitting || !query.trim() || !vertical || (vertical === "Others" && !other.trim())}>
          {submitting ? "Saving…" : "Submit feedback"}
        </button>
        <button className="ghost" onClick={reset} disabled={submitting}>Skip</button>
      </div>
    </>
  );
}

// ── RowWithDrawer ─────────────────────────────────────────────────────────────
// tier_label is carried in the data payload (needed for export) but is NOT
// shown anywhere in the table or drawer.
function RowWithDrawer({ r, isExpanded, onToggle }: { r: PreviewRow; isExpanded: boolean; onToggle: () => void }) {
  const tone = anchorTone(r.anchor_source);
  return (
    <>
      <tr className={isExpanded ? "row-open" : ""}>
        <td className="td-x">
          <span className={`row-caret ${isExpanded ? "open" : ""}`} onClick={onToggle} style={{ cursor: "pointer" }}>▶</span>
        </td>
        <td style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--muted)" }}>{r.rank}</td>
        <td>
          <a href={r.page} target="_blank" rel="noopener noreferrer" className="page-short" title={r.page}>
            {shortPath(r.page)}
          </a>
        </td>
        <td><span className="matched-tag">{r.matched_on}</span></td>
        <td><ScoreBar value={r.topical_relevance_score} /></td>
        <td><ScoreBar value={r.seo_score} /></td>
        <td>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {r.page_category && r.page_category !== "Other" && <span className="cat">{r.page_category}</span>}
            <ContentTypeBadge type={r.content_type as ContentType} />
          </div>
        </td>
      </tr>
      {isExpanded && (
        <tr className="drawer-row">
          <td colSpan={7}>
            <div className="drawer">
              <div className="drawer-field"><span>URL</span><a href={r.page} target="_blank" rel="noopener noreferrer">{r.page}</a></div>
              <div className="drawer-field"><span>Top Query</span>{r.query}</div>
              {r.content_type && <div className="drawer-field"><span>Content Type</span><ContentTypeBadge type={r.content_type as ContentType} /></div>}
              {r.anchor_text && (
                <div className="drawer-field">
                  <span>Anchor Text</span>
                  <span>
                    <span className={`anchor anchor-${tone}`}>{r.anchor_text}</span>
                    <span className="anchor-note"> · {ANCHOR_NOTE[tone]}</span>
                  </span>
                </div>
              )}
              {r.meta_title && <div className="drawer-field"><span>Title</span>{r.meta_title}</div>}
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

export default function Wizard() {
  const [step, setStep] = useState(1);
  const [domains, setDomains] = useState<Domain[]>([]);
  const [domainErr, setDomainErr] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Active vertical filter chip — "All" shows every domain regardless of vertical.
  const [verticalFilter, setVerticalFilter] = useState<string>("All");
  const [clientName, setClientName] = useState("");
  const [topic, setTopic] = useState("");
  const init = useMemo(presetDates, []);
  const [startDate, setStartDate] = useState(init.start);
  const [endDate, setEndDate] = useState(init.end);
  const [formErr, setFormErr] = useState("");
  const [stages, setStages] = useState<Record<StageId, StageStatus>>({
    fetch: "idle", pages: "idle", seo: "idle", metadata: "idle", match: "idle", refine: "idle", rank: "idle",
  });
  const [logLines, setLogLines] = useState<string[]>([]);
  const [funnel, setFunnel] = useState<{ rowsFetched: number; pages: number; matched: number; scored: number } | null>(null);
  const [preview, setPreview] = useState<PreviewRow[]>([]);
  const [exportRows, setExportRows] = useState<ExportRow[]>([]);
  const [runError, setRunError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const consoleRef = useRef<HTMLDivElement>(null);
  const [consoleOpen, setConsoleOpen] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exportUrl, setExportUrl] = useState("");
  const [filename, setFilename] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [authReady, setAuthReady] = useState<boolean | null>(null);

  useEffect(() => {
    fetch("/api/auth/status", { cache: "no-store" })
      .then((r) => {
        if (r.status === 401) { window.location.href = "/login/"; return null; }
        return r.json();
      })
      .then((s) => {
        if (!s) return;
        const ready = Boolean(s.ready);
        setAuthReady(ready);
        if (ready) {
          fetch("/api/domains").then((r) => r.json()).then((d) => {
            if (d.error) setDomainErr(d.error);
            else setDomains(d.domains ?? []);
          }).catch((e) => setDomainErr(String(e)));
        }
      }).catch(() => setAuthReady(false));
  }, []);

  useEffect(() => { consoleRef.current?.scrollTo(0, consoleRef.current.scrollHeight); }, [logLines]);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => { if (running && elapsedSec === 90) setConsoleOpen(true); }, [elapsedSec, running]);
  useEffect(() => { if (running && stages.refine === "active") setConsoleOpen(true); }, [running, stages.refine]);

  const elapsedLabel = (() => {
    const mm = Math.floor(elapsedSec / 60), ss = elapsedSec % 60;
    return mm > 0 ? `${mm}m ${ss}s` : `${ss}s`;
  })();

  // Vertical chips — built from whatever verticals are present in the loaded list,
  // ordered by VERTICAL_ORDER. "All" and "FFG" are excluded: Row 1 quick-picks
  // (FFG only / Clients only / Select all / Clear) already handle those; the chip
  // row starts at Nonprofit and is purely for filtering by client vertical.
  const presentVerticals = useMemo(() => {
    const seen = new Set<string>();
    for (const d of domains) seen.add(d.vertical ?? "Other");
    return VERTICAL_ORDER.filter((v) => v !== "FFG" && seen.has(v));
  }, [domains]);

  // Domains visible in the grid after applying the active vertical chip filter.
  const visibleDomains = useMemo(() => {
    if (verticalFilter === "All") return domains;
    return domains.filter((d) => (d.vertical ?? "Other") === verticalFilter);
  }, [domains, verticalFilter]);

  const toggle = (siteUrl: string) => setSelected((prev) => { const next = new Set(prev); next.has(siteUrl) ? next.delete(siteUrl) : next.add(siteUrl); return next; });

  // Quick-pick buttons always operate on the full domain list, not just the
  // currently filtered view, so "Select all" always means all domains.
  const pick = (which: "ffg" | "clients" | "all" | "clear") => {
    if (which === "clear") return setSelected(new Set());
    if (which === "all") return setSelected(new Set(domains.map((d) => d.siteUrl)));
    setSelected(new Set(domains.filter((d) => (which === "ffg" ? d.isFfg : !d.isFfg)).map((d) => d.siteUrl)));
  };
  const applyPreset = () => { const p = presetDates(); setStartDate(p.start); setEndDate(p.end); };
  const toggleRow = (rank: number) => setExpanded((prev) => { const next = new Set(prev); next.has(rank) ? next.delete(rank) : next.add(rank); return next; });

  const doDownload = async () => {
    setDownloading(true);
    try {
      const resp = await fetch("/api/download", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows: exportRows, filename }) });
      if (!resp.ok) { const j = await resp.json().catch(() => ({})); throw new Error(j.error || `Download failed (${resp.status}).`); }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = filename.match(/\.xlsx$/i) ? filename : `${filename || "FFG-Placements"}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
    } catch (e) { setRunError(e instanceof Error ? e.message : String(e)); } finally { setDownloading(false); }
  };

  const goRun = async () => {
    if (!topic.trim()) return setFormErr("Enter a topic before running");
    setFormErr(""); setStep(3); setRunError(null); setExportUrl(""); setPreview([]); setExportRows([]);
    setFunnel(null); setLogLines([]); setShowAll(false); setExpanded(new Set());
    setStages({ fetch: "idle", pages: "idle", seo: "idle", metadata: "idle", match: "idle", refine: "idle", rank: "idle" });
    setElapsedSec(0); setRunning(true);
    const slug = (s: string) => s.replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    setFilename(`${slug(clientName || "Client")}-Placements-${slug(topic)}.xlsx`);
    try {
      const resp = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domains: [...selected], topic: topic.trim(), startDate, endDate }) });
      if (resp.status === 401) { window.location.href = "/login/"; return; }
      if (!resp.ok || !resp.body) { const j = await resp.json().catch(() => ({})); throw new Error(j.error || `Run failed (${resp.status}).`); }
      const reader = resp.body.getReader(); const decoder = new TextDecoder(); let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n"); buffer = parts.pop() ?? "";
        for (const part of parts) { if (!part.trim()) continue; handleEvent(JSON.parse(part)); }
      }
    } catch (e) { setRunError(e instanceof Error ? e.message : String(e)); } finally { setRunning(false); }
  };

  function handleEvent(e: any) {
    if (e.type === "stage") setStages((prev) => ({ ...prev, [e.stage]: e.status }));
    else if (e.type === "log") setLogLines((prev) => [...prev, e.message]);
    else if (e.type === "funnel") setFunnel(e);
    else if (e.type === "error") setRunError(e.message);
    else if (e.type === "result") { setExportRows(e.rows); setPreview(e.preview); }
  }

  const doExport = async () => {
    setExporting(true); setExportUrl("");
    try {
      const resp = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows: exportRows, filename }) });
      const j = await resp.json();
      if (j.error) throw new Error(j.error);
      setExportUrl(j.url);
    } catch (e) { setRunError(e instanceof Error ? e.message : String(e)); } finally { setExporting(false); }
  };

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
            <p className="lead">Both the data@ and analytics@ accounts need to be connected before you can run a placement analysis.</p>
          </div>
          <div className="panel">
            <h2>Account access required</h2>
            <p className="panel-sub">Tokens are stored securely and refresh automatically.</p>
            <div className="row"><a href="/setup"><button className="primary">Go to setup →</button></a></div>
          </div>
        </div>
      </>
    );
  }

  const visible = showAll ? preview : preview.slice(0, PREVIEW_COLLAPSED);
  const activeStage = STAGES.find((s) => stages[s.id] === "active");
  const doneCount = STAGES.filter((s) => stages[s.id] === "done").length;

  return (
    <>
      <TopBar connected={authReady === true} navAction={nav} />
      <div className="shell">
        <div className="page-head">
          <h1>Placement Intelligence</h1>
          <p className="lead">Discover high-value content placement opportunities across the FFG network and client sites, ranked by topical relevance and SEO strength</p>
        </div>

        <Stepper step={step} />

        {/* ── Step 1: Domain picker ──────────────────────────────────────────── */}
        {step === 1 && (
          <div className="panel">
            <h2>Select domains</h2>
            <p className="panel-sub">Choose the properties to analyze. FFG-owned and client domains are tagged.</p>
            {domainErr && <div className="notice error" style={{ marginBottom: 16 }}>{domainErr}</div>}

            {/* Quick-pick buttons + selected count */}
            <div className="row" style={{ marginBottom: 12 }}>
              <button className="tiny" onClick={() => pick("ffg")}>FFG only</button>
              <button className="tiny" onClick={() => pick("clients")}>Clients only</button>
              <button className="tiny" onClick={() => pick("all")}>Select all</button>
              <button className="tiny ghost" onClick={() => pick("clear")}>Clear</button>
              <div className="spacer" />
              <span style={{ color: "var(--muted)", fontSize: 12.5, fontWeight: 500 }}>{selected.size} of {domains.length} selected</span>
            </div>

            {/* Vertical filter chips — built dynamically from loaded domain list.
                Shows client verticals only (All and FFG handled by Row 1 quick-picks).
                Clicking a chip filters the grid below; does not affect selection. */}
            {presentVerticals.length > 0 && (
              <div className="vertical-chips" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14 }}>
                {presentVerticals.map((v) => {
                  const count = domains.filter((d) => (d.vertical ?? "Other") === v).length;
                  const active = verticalFilter === v;
                  return (
                    <button
                      key={v}
                      className={`tiny${active ? " active-chip" : ""}`}
                      style={{
                        background: active ? "var(--navy)" : undefined,
                        color: active ? "#fff" : undefined,
                        borderColor: active ? "var(--navy)" : undefined,
                      }}
                      onClick={() => setVerticalFilter(active ? "All" : v)}
                    >
                      {v} <span style={{ opacity: 0.7, fontSize: 10.5, marginLeft: 3 }}>{count}</span>
                    </button>
                  );
                })}
              </div>
            )}

            {/* Flat domain grid — identical to M5 layout, filtered by active chip */}
            <div className="domain-grid">
              {visibleDomains.map((d) => (
                <label key={d.siteUrl} className={`domain ${selected.has(d.siteUrl) ? "sel" : ""}`}>
                  <input type="checkbox" checked={selected.has(d.siteUrl)} onChange={() => toggle(d.siteUrl)} />
                  {d.short}
                  <span className="tag">{d.isFfg ? "FFG" : "client"}</span>
                </label>
              ))}
              {!domains.length && !domainErr && <span style={{ color: "var(--muted)" }}>Loading properties…</span>}
              {domains.length > 0 && visibleDomains.length === 0 && (
                <span style={{ color: "var(--muted)" }}>No domains in this vertical</span>
              )}
            </div>

            <div className="row end" style={{ marginTop: 22 }}>
              <button className="primary" disabled={!selected.size} onClick={() => { setDomainErr(""); setStep(2); }}>Continue →</button>
            </div>
            {!selected.size && <p className="helptext">Select at least one domain to continue.</p>}
          </div>
        )}

        {/* ── Step 2: Topic & date range ─────────────────────────────────────── */}
        {step === 2 && (
          <div className="panel">
            <h2>Topic &amp; date range</h2>
            <p className="panel-sub">Describe what you&apos;re placing. The engine matches it against each page&apos;s search demand and content.</p>
            <label className="field">
              <span>Client name (optional, used in the export filename)</span>
              <input type="text" value={clientName} onChange={(e) => setClientName(e.target.value)} placeholder="e.g. Double the Donation" />
            </label>
            <label className="field">
              <span>Topic</span>
              <textarea rows={2} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. nonprofit CRM software" />
            </label>
            <label className="field" style={{ marginBottom: 8 }}><span>Date range</span></label>
            <div className="row" style={{ marginBottom: 4 }}>
              <button className="tiny" onClick={applyPreset}>Last 12 months</button>
              <span className="range-label">{startDate} → {endDate}</span>
            </div>
            <details className="disclosure">
              <summary>Custom range</summary>
              <div className="disclosure-body">
                <div className="row">
                  <label className="field" style={{ flex: 1, marginBottom: 0 }}><span>Start date</span><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></label>
                  <label className="field" style={{ flex: 1, marginBottom: 0 }}><span>End date</span><input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
                </div>
              </div>
            </details>
            {formErr && <div className="notice error" style={{ marginTop: 16 }}>{formErr}</div>}
            <div className="row end" style={{ marginTop: 18 }}>
              <button className="accent" onClick={goRun}>Run analysis →</button>
            </div>
          </div>
        )}

        {/* ── Step 3: Results ────────────────────────────────────────────────── */}
        {step === 3 && (
          <div className="panel">
            <div className="section-rule"><span>Progress</span></div>
            {running && activeStage && (
              <div className="run-status">
                Running, stage {doneCount + 1} of {STAGES.length}, {activeStage.label}
                <span className="run-elapsed">{elapsedLabel}</span>
              </div>
            )}
            {!running && !runError && preview.length > 0 && (
              <div className="run-status">Complete, {preview.length} pages scored</div>
            )}
            <div className="tracker">
              {STAGES.map((s) => (
                <div key={s.id} className={`tracker-stage ${stages[s.id]}`}>{s.label}</div>
              ))}
            </div>
            {running && (
              <div className="progress-wrap" aria-hidden="true">
                <div className="progress-bar" />
              </div>
            )}
            {running && stages.fetch === "active" && (
              <div className="patience-note">
                Fetching GSC data — this can take several minutes for large domain sets
              </div>
            )}
            {running && stages.refine === "active" && (
              <div className="patience-note">
                AI scoring in progress, this is the slowest stage and can take 3 to 5 minutes on a large domain set
              </div>
            )}
            {funnel && (
              <div className="funnel-line">
                <b>{funnel.rowsFetched.toLocaleString()}</b> GSC rows → <b>{funnel.pages.toLocaleString()}</b> pages → <b>{funnel.matched.toLocaleString()}</b> matched → <b>{funnel.scored.toLocaleString()}</b> scored
              </div>
            )}
            <details className="console-wrap" open={consoleOpen} onToggle={(e) => setConsoleOpen((e.currentTarget as HTMLDetailsElement).open)}>
              <summary className="console-summary">Fetch details</summary>
              <div className="console" ref={consoleRef}>
                {logLines.map((l, i) => <div key={i} className={`line ${i === logLines.length - 1 ? "new" : ""}`}>{l}</div>)}
                {!logLines.length && <div className="line">Starting pipeline…</div>}
              </div>
            </details>

            {runError && <div className="notice error" style={{ marginBottom: 18 }}>{runError}</div>}

            {preview.length > 0 && (
              <>
                <div className="section-rule"><span>Results</span></div>
                <div className="results-meta">
                  <span style={{ color: "var(--muted)", fontSize: 12.5 }}>
                    Showing {visible.length} of {preview.length}
                  </span>
                  {preview.length > PREVIEW_COLLAPSED && (
                    <button className="ghost tiny" onClick={() => setShowAll(!showAll)}>
                      {showAll ? "Show fewer" : `Show all ${preview.length}`}
                    </button>
                  )}
                </div>
                <div className="results-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th className="th-x" />
                        <th title="Rank">#</th>
                        <th title="Page URL">Page</th>
                        <th title="Which signal(s) matched the topic">Matched on</th>
                        <th title="Topical relevance score (0–10)">Relevance</th>
                        <th title="SEO strength score (0–10)">SEO</th>
                        <th title="Page category and content type">Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visible.map((r) => (
                        <RowWithDrawer key={r.rank} r={r} isExpanded={expanded.has(r.rank)} onToggle={() => toggleRow(r.rank)} />
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* ── Improve future results ─────────────────────────────────── */}
                <details style={{ marginTop: 22 }}>
                  <summary style={{ cursor: "pointer", fontSize: 13, color: "var(--teal-600)", marginBottom: 14 }}>Improve future results</summary>
                  <div style={{ paddingTop: 4 }}>
                    <p className="feedback-note">
                      Submitted feedback will not affect the current run. It will
                      be reviewed by the development team and used to improve
                      scoring in future versions.
                    </p>
                    <FeedbackPanel topic={topic} domains={[...selected]} />
                  </div>
                </details>

                {/* ── Sticky export bar ──────────────────────────────────────── */}
                <div className="export-bar">
                  <span className="export-count"><b>{exportRows.length}</b> rows ready</span>
                  <input
                    className="field export-filename"
                    style={{ margin: 0, padding: "7px 10px", fontSize: 13 }}
                    value={filename}
                    onChange={(e) => setFilename(e.target.value)}
                    placeholder="filename.xlsx"
                  />
                  <button className="accent tiny" onClick={doExport} disabled={exporting || !exportRows.length}>
                    {exporting ? "Exporting…" : "Export to Drive"}
                  </button>
                  <button className="ghost tiny" onClick={doDownload} disabled={downloading || !exportRows.length}>
                    {downloading ? "Downloading…" : "Download"}
                  </button>
                  {exportUrl && <a href={exportUrl} target="_blank" rel="noopener noreferrer" className="export-link">Open in Drive ↗</a>}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}



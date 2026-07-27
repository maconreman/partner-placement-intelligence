// ─────────────────────────────────────────────────────────────────────────────
// pipeline.ts  —  Orchestrator. Mirrors the Stage 1→9 run sequence in CELL 5.
//
// Stage order is identical to the notebook:
//   1 fetch → 2 aggregate(classify) → enrich(L3) → hub → programmatic →
//   3 SEO score → 4 metadata crawl(merge) → 5 quick-match → 6 HF relevance →
//   7 composite rank (dedup AFTER composite) → 8 export columns.
//
// Emits structured events so the API route can stream live progress to the
// wizard (the _fetch_log + stage-tracker analog).
// ─────────────────────────────────────────────────────────────────────────────
import { fetchAllDomains } from "./gsc";
import { aggregateToPages, computeSeoScore } from "./aggregate";
import { enrichPageCategories, detectHubPages, detectProgrammaticSeries } from "./classify";
import { crawlMetadataForPages } from "./crawl";
import { quickMatchCandidates } from "./quickmatch";
import { computeRelevanceScores, warmupEmbedModel, HFAPIError } from "./relevance";
import { buildResults, toExportRows } from "./rank";
import { ResultRow } from "./util";

export type StageId = "fetch" | "pages" | "seo" | "metadata" | "match" | "refine" | "rank";

export type PipelineEvent =
  | { type: "stage"; stage: StageId; status: "active" | "done" | "idle" }
  | { type: "log"; message: string }
  | { type: "funnel"; rowsFetched: number; pages: number; matched: number; scored: number }
  | { type: "error"; code: "no_data" | "no_relevance" | "hf_error"; message: string }
  | { type: "result"; rows: ReturnType<typeof toExportRows>; preview: ResultRow[] };

export interface RunParams {
  domains: string[];
  topic: string;
  startDate: string;
  endDate: string;
}

export async function runPipeline(
  { domains, topic, startDate, endDate }: RunParams,
  emit: (e: PipelineEvent) => void
): Promise<void> {
  const log = (message: string) => emit({ type: "log", message });

  // Stage 1 — Fetch (cache-first) + HF warm-up
  emit({ type: "stage", stage: "fetch", status: "active" });
  warmupEmbedModel(); // fire and forget
  const rows = await fetchAllDomains(domains, startDate, endDate, log);
  emit({ type: "stage", stage: "fetch", status: "done" });
  if (!rows.length) {
    emit({ type: "error", code: "no_data", message: "No GSC data returned. Try a wider date range." });
    return;
  }
  const rowsFetched = rows.length;

  // Stage 2 — Unique pages (classify + aggregate), then L3 → hub → programmatic
  emit({ type: "stage", stage: "pages", status: "active" });
  let pages = aggregateToPages(rows);
  pages = enrichPageCategories(pages, log);
  pages = detectHubPages(pages, log);
  pages = detectProgrammaticSeries(pages, log);
  emit({ type: "stage", stage: "pages", status: "done" });
  const nPages = pages.length;

  // Stage 3 — SEO score (no gating influence)
  emit({ type: "stage", stage: "seo", status: "active" });
  const seo = computeSeoScore(pages);
  pages.forEach((p, i) => { p.seo_score = seo[i]; });
  emit({ type: "stage", stage: "seo", status: "done" });

  // Stage 4 — Universal metadata crawl, merged onto pages
  emit({ type: "stage", stage: "metadata", status: "active" });
  const meta = await crawlMetadataForPages(pages, log);
  const metaByPage = new Map(meta.map((m) => [m.page, m]));
  for (const p of pages) {
    const m = metaByPage.get(p.page);
    p.meta_title = m?.meta_title ?? "";
    p.meta_description = m?.meta_description ?? "";
    p.h1 = m?.h1 ?? "";
    p.h2 = m?.h2 ?? "";
  }
  emit({ type: "stage", stage: "metadata", status: "done" });

  // Stage 5 — Quick match (lexical filter)
  emit({ type: "stage", stage: "match", status: "active" });
  const cand = quickMatchCandidates(pages, topic, undefined, log);
  emit({ type: "stage", stage: "match", status: "done" });
  const nMatched = cand.length;
  if (!cand.length) {
    emit({ type: "funnel", rowsFetched, pages: nPages, matched: 0, scored: 0 });
    emit({ type: "error", code: "no_relevance", message: "No relevant pages found. Try a broader topic." });
    return;
  }

  // Stage 6 — Relevance (HF refine). No TF-IDF fallback — HFAPIError surfaces.
  emit({ type: "stage", stage: "refine", status: "active" });
  let scores: number[];
  try {
    scores = await computeRelevanceScores(cand, topic, log);
  } catch (exc) {
    emit({ type: "stage", stage: "refine", status: "idle" });
    if (exc instanceof HFAPIError) {
      emit({ type: "error", code: "hf_error", message: exc.message });
      return;
    }
    throw exc;
  }
  cand.forEach((c, i) => { c.topical_relevance_score = scores[i]; });
  emit({ type: "stage", stage: "refine", status: "done" });

  // Stage 7 — Composite rank (composite BEFORE dedup)
  emit({ type: "stage", stage: "rank", status: "active" });
  const results = buildResults(cand);
  emit({ type: "stage", stage: "rank", status: "done" });
  if (!results.length) {
    emit({ type: "funnel", rowsFetched, pages: nPages, matched: nMatched, scored: 0 });
    emit({ type: "error", code: "no_relevance", message: "No relevant pages found. Try a broader topic." });
    return;
  }

  emit({ type: "funnel", rowsFetched, pages: nPages, matched: nMatched, scored: results.length });
  emit({ type: "result", rows: toExportRows(results), preview: results.slice(0, 10) });
}

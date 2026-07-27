// ─────────────────────────────────────────────────────────────────────────────
// rank.ts  —  Port of the Stage 7 composite-rank block (CELL 5)
//
// Hard rule preserved: composite scores are computed BEFORE de-duplication, so
// the best-scoring query per page survives the drop_duplicates. Then tier label,
// blog tiebreaker, rank assignment, and the workflow-first export column order.
// ─────────────────────────────────────────────────────────────────────────────
import {
  COMPOSITE_WEIGHT_RELEVANCE, COMPOSITE_WEIGHT_SEO, TIER_PRIORITY, TIER_STRONG,
  SCORE_MIN, SCORE_MAX, EXPORT_LABELS,
} from "./config";
import { CandidateRow, ResultRow } from "./util";

function tierLabel(score: number): string {
  if (score >= TIER_PRIORITY) return "Priority";
  if (score >= TIER_STRONG) return "Strong";
  return "Monitor";
}

const TIER_ORDER: Record<string, number> = { Priority: 0, Strong: 1, Monitor: 2 };

export function buildResults(cand: CandidateRow[]): ResultRow[] {
  // Clamp scores into range (validate_scores equivalent).
  for (const c of cand) {
    if (c.topical_relevance_score !== undefined) {
      c.topical_relevance_score = Math.min(Math.max(c.topical_relevance_score, SCORE_MIN), SCORE_MAX);
    }
    if (c.seo_score !== undefined) {
      c.seo_score = Math.min(Math.max(c.seo_score, SCORE_MIN), SCORE_MAX);
    }
  }

  // Precision floor: Cell 5's existing > 0.0 filter.
  const scored = cand.filter((c) => (c.topical_relevance_score ?? 0) > 0.0);
  if (!scored.length) return [];

  // Composite BEFORE dedup.
  const withComposite = scored.map((c) => ({
    ...c,
    composite_score:
      (c.topical_relevance_score ?? 0) * COMPOSITE_WEIGHT_RELEVANCE +
      (c.seo_score ?? 0) * COMPOSITE_WEIGHT_SEO,
  }));

  // Sort by composite desc, keep first occurrence per page (best query survives).
  withComposite.sort((a, b) => b.composite_score - a.composite_score);
  const seen = new Set<string>();
  const deduped: typeof withComposite = [];
  for (const r of withComposite) {
    if (seen.has(r.page)) continue;
    seen.add(r.page);
    deduped.push(r);
  }

  // Tier + blog tiebreaker, then ranked sort.
  const ranked: ResultRow[] = deduped.map((r) => ({
    ...r,
    tier_label: tierLabel(r.topical_relevance_score ?? 0),
    is_blog_flag: r.page_category !== "Blog" ? 1 : 0,
    rank: 0,
  }));

  ranked.sort((a, b) => {
    const ta = TIER_ORDER[a.tier_label] ?? 2;
    const tb = TIER_ORDER[b.tier_label] ?? 2;
    if (ta !== tb) return ta - tb;
    if (a.is_blog_flag !== b.is_blog_flag) return a.is_blog_flag - b.is_blog_flag;
    return b.composite_score - a.composite_score;
  });

  ranked.forEach((r, i) => { r.rank = i + 1; });
  return ranked;
}

// Workflow-first export column order (Rank → placement context → identity →
// on-page content → traffic signals). Domain / GSC Account intentionally omitted.
export function toExportRows(results: ResultRow[]): Record<string, string | number>[] {
  return results.map((r) => ({
    [EXPORT_LABELS.rank]: r.rank,
    Page: r.page,
    "Matched On": r.matched_on ?? "",
    "Anchor Text": r.anchor_text ?? "",
    [EXPORT_LABELS.relevance]: r.topical_relevance_score ?? 0,
    [EXPORT_LABELS.seo]: r.seo_score ?? 0,
    [EXPORT_LABELS.type]: r.page_category === "Other" ? "" : r.page_category,
    [EXPORT_LABELS.title]: r.meta_title ?? "",
    [EXPORT_LABELS.metaDesc]: r.meta_description ?? "",
    [EXPORT_LABELS.h1]: r.h1 ?? "",
    [EXPORT_LABELS.h2]: r.h2 ?? "",
    "Top Query": r.query ?? "",
    Clicks: r.clicks,
    Impressions: r.impressions,
    Position: r.position,
  }));
}

// ─────────────────────────────────────────────────────────────────────────────
// aggregate.ts  —  Port of aggregate_to_pages() + compute_seo_score() (CELL 4)
//
// Collapse GSC query rows to unique pages: sum clicks/impressions, impression-
// weight position, capture top query (by clicks) and a deduped query bag (cap 50).
// SEO score: percentile-rank blend 50% clicks / 30% impressions / 20% position.
// ─────────────────────────────────────────────────────────────────────────────
import { SEO_WEIGHT_CLICKS, SEO_WEIGHT_IMPRESSIONS, SEO_WEIGHT_POSITION, SCORE_MIN, SCORE_MAX } from "./config";
import { classifyPage } from "./classify";
import { GscRow, PageRow, percentileNorm, clampRound1 } from "./util";

const QUERY_BAG_CAP = 50;

export function aggregateToPages(rows: GscRow[]): PageRow[] {
  if (!rows.length) return [];

  // Group by (account, domain, page) — classification is per page.
  const groups = new Map<string, {
    account: string; domain: string; page: string; page_category: string;
    clicks: number; impressions: number; pos_x_imp: number;
    rows: GscRow[];
  }>();

  for (const r of rows) {
    const key = `${r.account}|${r.domain}|${r.page}`;
    let g = groups.get(key);
    if (!g) {
      g = {
        account: r.account, domain: r.domain, page: r.page,
        page_category: classifyPage(r.page),
        clicks: 0, impressions: 0, pos_x_imp: 0, rows: [],
      };
      groups.set(key, g);
    }
    g.clicks += r.clicks;
    g.impressions += r.impressions;
    g.pos_x_imp += r.position * r.impressions;
    g.rows.push(r);
  }

  const out: PageRow[] = [];
  for (const g of groups.values()) {
    const position = g.impressions > 0 ? Math.round((g.pos_x_imp / g.impressions) * 100) / 100 : 0.0;
    const ordered = [...g.rows].sort((a, b) => b.clicks - a.clicks);
    const topQuery = ordered.length ? String(ordered[0].query ?? "") : "";

    const seen: string[] = [];
    for (const r of ordered) {
      const q = String(r.query ?? "");
      if (q && !seen.includes(q)) seen.push(q);
      if (seen.length >= QUERY_BAG_CAP) break;
    }

    out.push({
      account: g.account, domain: g.domain, page: g.page, page_category: g.page_category,
      clicks: g.clicks, impressions: g.impressions, position,
      query: topQuery, query_all: seen.join(" | "),
    });
  }
  return out;
}

export function computeSeoScore(pages: PageRow[]): number[] {
  const clicksPct = percentileNorm(pages.map((p) => p.clicks));
  const impressPct = percentileNorm(pages.map((p) => p.impressions));
  const posPctRaw = percentileNorm(pages.map((p) => p.position));
  const posPct = posPctRaw.map((v) => 1.0 - v); // lower position is better

  return pages.map((_, i) => {
    const raw = clicksPct[i] * SEO_WEIGHT_CLICKS
      + impressPct[i] * SEO_WEIGHT_IMPRESSIONS
      + posPct[i] * SEO_WEIGHT_POSITION;
    return clampRound1(raw * 10, SCORE_MIN, SCORE_MAX);
  });
}

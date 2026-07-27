// ─────────────────────────────────────────────────────────────────────────────
// types.ts + util.ts merged — shared row shapes and the numeric helpers that
// replace pandas / numpy / scipy / sklearn calls from the notebook.
// ─────────────────────────────────────────────────────────────────────────────

// ── Row shapes (replace the notebook's pandas frames) ───────────────────────
export interface GscRow {
  account: string;
  domain: string;
  query: string;
  page: string;
  clicks: number;
  impressions: number;
  position: number;
}

export interface PageRow {
  account: string;
  domain: string;
  page: string;
  page_category: string;
  clicks: number;
  impressions: number;
  position: number;
  query: string;        // top query (highest clicks)
  query_all: string;    // pipe-joined query bag (cap 50)
  seo_score?: number;
  meta_title?: string;
  meta_description?: string;
  h1?: string;
  h2?: string;
}

export interface CandidateRow extends PageRow {
  matched_on: string;
  anchor_text: string;
  anchor_source: string;
  lexical_score: number;
  surface_max?: number;
  topical_relevance_score?: number;
}

export interface ResultRow extends CandidateRow {
  composite_score: number;
  tier_label: string;
  is_blog_flag: number;
  rank: number;
}

export interface MetaRow {
  page: string;
  meta_title: string;
  meta_description: string;
  h1: string;
  h2: string;
}

export interface FeedbackRow {
  query: string;
  vertical: string;
  category: string;
  topic: string;
  domains: string;
}

export type LogFn = (msg: string) => void;

// ── URL helpers ─────────────────────────────────────────────────────────────
// urlparse(...).path equivalent: returns lowercased pathname, hash already gone.
export function urlPath(url: string): string {
  try {
    const raw = url.split("#")[0];
    return new URL(raw).pathname.toLowerCase();
  } catch {
    return "";
  }
}

export function urlHost(url: string): string {
  try {
    return new URL(url.split("#")[0]).host.replace(/^www\./, "");
  } catch {
    return "";
  }
}

// ── scipy.stats.rankdata(method="average") → ranks/n in [0,1] ────────────────
export function percentileNorm(values: number[]): number[] {
  const n = values.length;
  if (n === 0) return [];
  const unique = new Set(values);
  if (unique.size <= 1) return values.map(() => 0.0);

  // Average ranks for ties (1-indexed, matching scipy).
  const idx = values.map((v, i) => ({ v, i }));
  idx.sort((a, b) => a.v - b.v);
  const ranks = new Array<number>(n).fill(0);
  let i = 0;
  while (i < n) {
    let j = i;
    while (j + 1 < n && idx[j + 1].v === idx[i].v) j++;
    const avgRank = (i + 1 + (j + 1)) / 2; // average of the 1-indexed positions
    for (let k = i; k <= j; k++) ranks[idx[k].i] = avgRank;
    i = j + 1;
  }
  return ranks.map((r) => r / n);
}

export function clampRound1(x: number, lo: number, hi: number): number {
  const c = Math.min(Math.max(x, lo), hi);
  return Math.round(c * 10) / 10;
}

// ── sklearn cosine_similarity(q, M) for a single query vector ───────────────
export function l2normalize(v: number[]): number[] {
  let s = 0;
  for (const x of v) s += x * x;
  const n = Math.sqrt(s) + 1e-9;
  return v.map((x) => x / n);
}

export function cosineToQuery(qUnit: number[], mat: number[][]): number[] {
  return mat.map((row) => {
    const r = l2normalize(row);
    let dot = 0;
    for (let i = 0; i < qUnit.length; i++) dot += qUnit[i] * r[i];
    return dot;
  });
}

// ── ThreadPoolExecutor(max_workers=N) → bounded promise pool ────────────────
export async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  const runners = new Array(Math.min(limit, items.length)).fill(0).map(async () => {
    while (true) {
      const i = cursor++;
      if (i >= items.length) break;
      results[i] = await worker(items[i], i);
    }
  });
  await Promise.all(runners);
  return results;
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));


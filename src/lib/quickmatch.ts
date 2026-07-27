// ─────────────────────────────────────────────────────────────────────────────
// quickmatch.ts  —  Port of quick_match_candidates() + _best_fragment() (CELL 4)
//
// Level 1 lexical pre-filter. Hard rules preserved:
//   • _DOMAIN_GENERICS filtered from distinct tokens alongside _GENERIC_TOKENS.
//   • Gate on the best SINGLE surface (surface_max >= gate_min), never the
//     cross-surface sum — a bigram must qualify within one surface.
//   • matched_on lists only independently-qualifying surfaces (score >= gate_min).
//   • anchor_text mirrors matched_on order: the exact fragment per surface.
//   • Hub pages are excluded; Programmatic pages are KEPT (reviewable).
//
// FIX (M3.4.1): Working filter replaced with shouldExcludePage() from classify.ts.
// Previously only Hub-category pages were excluded here; Homepage, Contact, and
// pages matching SKIP_URL_PATTERNS (/category/*, /tag/*, etc.) could still reach
// scoring via their query_all surface. shouldExcludePage() is the same gate used
// in crawl.ts Level 0, making the two stages consistent.
// ─────────────────────────────────────────────────────────────────────────────
import { NLP_SHORTLIST_CAP, _DOMAIN_GENERICS } from "./config";
import { PageRow, CandidateRow, LogFn, urlPath } from "./util";
import { shouldExcludePage } from "./classify";

const GENERIC_TOKENS = new Set([
  "software", "platform", "platforms", "tool", "tools", "service", "services",
  "solution", "solutions", "system", "systems", "app", "apps", "online",
  "best", "top", "company", "companies", "provider", "providers", "management",
]);
const MATCH_STOPWORDS = new Set(["the", "and", "for", "with", "your", "you", "our", "from", "that", "this", "are"]);
const STOP_SLUGS = new Set(["www", "com", "org", "net", "co", "html", "php", "aspx", "index"]);

// Cascade order is load-bearing — matched_on / anchor_text follow it.
const MATCH_CASCADE: Array<[string, "slug" | "meta" | "h1" | "h2" | "query_all"]> = [
  ["Slug", "slug"],
  ["Meta+Title", "meta"],
  ["H1", "h1"],
  ["H2", "h2"],
  ["Query", "query_all"],
];

function slugText(url: string): string {
  const path = urlPath(url);
  return path.split(/[-_/]/).filter((t) => t && !STOP_SLUGS.has(t)).join(" ");
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function quickMatchCandidates(
  pages: PageRow[],
  rawTopic: string,
  cap = NLP_SHORTLIST_CAP,
  log: LogFn = console.log
): CandidateRow[] {
  if (!pages.length) return [];

  // Unified exclusion gate — mirrors the Level 0 filter in crawl.ts exactly.
  // Drops Homepage, Contact, Hub, taxonomy archives (/category/*, /tag/*, etc.),
  // bare section roots (/blog/, /resources/, etc.), and utility URL patterns.
  // Programmatic 'Other' pages are intentionally kept for review.
  const working = pages.filter((p) => !shouldExcludePage(p.page, p.page_category));
  if (!working.length) return [];

  const topic = rawTopic.toLowerCase().trim();
  const rawTokens = topic.match(/[a-z0-9]+/g) ?? [];

  let distinct = rawTokens.filter(
    (t) => t.length > 2 && !GENERIC_TOKENS.has(t) && !_DOMAIN_GENERICS.has(t) && !MATCH_STOPWORDS.has(t)
  );
  if (!distinct.length) distinct = rawTokens.filter((t) => t.length > 1 && !_DOMAIN_GENERICS.has(t));
  if (!distinct.length) distinct = rawTokens.filter((t) => t.length > 1);

  const bigrams: string[] = [];
  for (let i = 0; i < rawTokens.length - 1; i++) bigrams.push(`${rawTokens[i]} ${rawTokens[i + 1]}`);
  const validBigrams = bigrams.filter((bg) => distinct.some((dt) => bg.includes(dt)));
  const tokenRes = distinct.map((t) => new RegExp(`\\b${escapeRegExp(t)}\\b`));

  // Bigram-level gate for multi-token topics; single-token fallback otherwise.
  const gateMin = distinct.length >= 2 ? 2 : 1;

  const sigScore = (text: string): number => {
    if (!text) return 0;
    const s = String(text).toLowerCase();
    let sc = 0;
    if (topic && s.includes(topic)) sc += 3;
    for (const bg of validBigrams) if (bg && s.includes(bg)) sc += 2;
    for (const rx of tokenRes) if (rx.test(s)) sc += 1;
    return sc;
  };

  const bestFragment = (text: string): [number, string] => {
    if (!text) return [0, ""];
    let fragments = String(text).split("|").map((f) => f.trim()).filter(Boolean);
    if (!fragments.length) fragments = [String(text).trim()];
    let bestSc = 0;
    let bestFrag = "";
    for (const f of fragments) {
      const sc = sigScore(f);
      if (sc > bestSc) {
        bestSc = sc;
        bestFrag = f;
      }
    }
    return [bestSc, bestFrag];
  };

  const surfaceValue = (p: PageRow, surface: "slug" | "meta" | "h1" | "h2" | "query_all"): string => {
    switch (surface) {
      case "slug": return slugText(p.page);
      case "meta": return `${p.meta_title ?? ""} | ${p.meta_description ?? ""}`;
      case "h1": return p.h1 ?? "";
      case "h2": return p.h2 ?? "";
      case "query_all": return p.query_all ?? "";
    }
  };

  const scored: CandidateRow[] = [];
  for (const p of working) {
    const labels: string[] = [];
    const anchors: string[] = [];
    let total = 0;
    let best = 0;
    let bestAnchorSc = 0;
    let bestAnchorSurface = "";
    for (const [label, surface] of MATCH_CASCADE) {
      const [sc, frag] = bestFragment(surfaceValue(p, surface));
      total += sc;
      if (sc > best) best = sc;
      if (sc >= gateMin) {
        labels.push(label);
        // Every qualifying surface contributes its verbatim fragment so the
        // anchor field is never blank (slug included). The fragment is always
        // pulled from the page's own slug / title / H1 / H2 / GSC query — never
        // generated. `anchor_source` records the surface of the strongest
        // fragment so the UI can flag non-headline sources (slug / meta / query
        // read as raw signal, not ready-to-use anchor copy).
        if (frag) anchors.push(frag);
        if (sc > bestAnchorSc && frag) {
          bestAnchorSc = sc;
          bestAnchorSurface = surface;
        }
      }
    }
    scored.push({
      ...p,
      matched_on: labels.join(" | "),
      anchor_text: anchors.filter(Boolean).join(" | "),
      anchor_source: bestAnchorSurface,
      lexical_score: total,
      surface_max: best,
    });
  }

  const cand = scored
    .filter((c) => (c.surface_max ?? 0) >= gateMin)
    .sort((a, b) => (b.lexical_score - a.lexical_score) || ((b.seo_score ?? 0) - (a.seo_score ?? 0)))
    .slice(0, cap)
    .map(({ surface_max, ...rest }) => rest); // drop helper column

  log(`▸ Quick match: ${cand.length} of ${working.length} unique pages qualified.`);
  return cand;
}

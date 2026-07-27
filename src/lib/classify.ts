// ─────────────────────────────────────────────────────────────────────────────
// classify.ts  —  Port of CELL 4 classification block (M3.4)
//
// Three-layer page classifier + corpus-aware Layer 3 inheritance + dual-mechanism
// hub detection + programmatic-series flagging. Hard rules preserved verbatim:
//   • Layer 3 must NOT inherit from the domain root (len(segs) < 2 → no parent).
//   • Hub detection is dual: data-driven (detect_hub_pages) AND vocabulary-driven
//     (_is_section_hub_url, applied in the Level 0 crawl filter).
//   • Programmatic series children are KEPT (re-tagged 'Other'), never dropped.
//
// FIX (M3.4.1): Added shouldExcludePage() — a single unified exclusion gate that
// is now called in BOTH crawl.ts (Level 0) and quickmatch.ts (working filter).
// Previously the Level 0 crawl filter and the quickmatch Hub-only filter were
// inconsistent: pages classified as Homepage/Contact, or matching SKIP_URL_PATTERNS
// (e.g. /category/*, /tag/*), were excluded from the metadata crawl but still
// reached quickmatch via their query_all surface. shouldExcludePage() closes that
// gap by centralising all exclusion logic in one place.
// ─────────────────────────────────────────────────────────────────────────────
import {
  CATEGORY_PATTERNS, CATEGORY_VOCAB, LAYER2_MIN_VOCAB_HITS, PROGRAMMATIC_SERIES_MIN,
} from "./config";
import { PageRow, LogFn, urlPath } from "./util";

const REAL_CATEGORIES = new Set(["Blog", "Landing Page", "Product / Service", "Contact"]);

// ── Layer 1 + Layer 2 ───────────────────────────────────────────────────────
export function classifyPage(url: string): string {
  let path: string;
  try {
    // Strip hash fragment — never part of server-side page identity (M3.4d).
    path = urlPath(url);
  } catch {
    return "Other";
  }
  if (path === "" || path === "/") return "Homepage";

  // Layer 1 — structural pattern match (exact, fast).
  for (const category of Object.keys(CATEGORY_PATTERNS)) {
    if (CATEGORY_PATTERNS[category].some((p) => path.includes(p))) return category;
  }

  // Layer 2 — slug vocabulary scoring (requires confident multi-token match).
  const tokens = new Set(path.split(/[-_/]/).filter(Boolean));
  let bestCat = "Other";
  let bestHits = 0;
  for (const category of Object.keys(CATEGORY_VOCAB)) {
    let hits = 0;
    for (const t of tokens) if (CATEGORY_VOCAB[category].has(t)) hits++;
    if (hits > bestHits) {
      bestCat = category;
      bestHits = hits;
    }
  }
  return bestHits >= LAYER2_MIN_VOCAB_HITS ? bestCat : "Other";
}

// ── Unified exclusion gate (M3.4.1) ─────────────────────────────────────────
// Returns true when a page must be dropped before scoring or crawling.
// Applied in BOTH crawl.ts (Level 0 filter) AND quickmatch.ts (working filter)
// so the two stages are always consistent.
//
// Exclusion criteria (any one is sufficient):
//   1. Category is Homepage, Contact, or Hub — not placeable content.
//   2. Path matches SKIP_URL_PATTERNS — structural/utility URLs that are never
//      placement candidates (taxonomy archives, auth pages, etc.).
//   3. Path is a bare section root — a URL whose path is a single well-known
//      section slug with no child segment (e.g. /blog/, /resources/). These are
//      listing/hub pages even when classifyPage() assigns them a content category
//      (e.g. /blog/ → "Blog") because detectHubPages() only fires when the domain
//      has enough child posts in the GSC date window.

// Taxonomy / utility URL patterns — extended to cover WordPress-style archives.
// Matches anywhere in the full path so /category/anything/ and /tag/foo/bar/ are
// both caught.  The blog-root pattern catches /blog and /blog/ as a bare root
// (terminal segment equals "blog") via BARE_SECTION_ROOTS below, so we no longer
// need the `\/blog\/?$` anchor here.
const SKIP_URL_PATTERNS =
  /\/(contact|privacy|terms|login|signin|cart|checkout|category|tag|author|page|feed|wp-json|wp-admin|wp-content|amp|search)\/?/i;

// Well-known section-root slugs. A path whose ONLY segment is one of these is a
// listing page (e.g. /blog/, /resources/, /guides/) and must be excluded even if
// classifyPage() returns a content category for it.
const BARE_SECTION_ROOTS = new Set([
  "blog", "blogs", "news", "insights", "resources", "resource",
  "articles", "article", "posts", "post", "updates", "update",
  "guides", "guide", "tips", "tip", "learn", "library",
  "press", "events", "event", "podcast", "podcasts", "webinars", "webinar",
  "case-studies", "case-study", "testimonials", "stories", "story",
]);

export function shouldExcludePage(url: string, page_category: string): boolean {
  // 1. Category-based exclusion.
  if (["Homepage", "Contact", "Hub"].includes(page_category)) return true;

  let path: string;
  try {
    path = new URL(url.split("#")[0]).pathname.replace(/\/+$/, "").toLowerCase();
  } catch {
    return true; // unparseable URL — exclude
  }

  // 2. Structural / utility URL pattern match.
  if (SKIP_URL_PATTERNS.test(path)) return true;

  // 3. Bare section root — path has exactly one non-empty segment that is a
  //    known listing slug. Catches /blog, /blog/, /resources/, /guides/ etc.
  const segs = path.split("/").filter(Boolean);
  if (segs.length === 1 && BARE_SECTION_ROOTS.has(segs[0])) return true;

  return false;
}

// (domain, parentPath) tuple key, or null for top-level pages (len(segs) < 2).
function parentKey(url: string): string | null {
  try {
    const parsed = new URL(url.split("#")[0]);
    const dom = parsed.host.replace(/^www\./, "");
    const segs = parsed.pathname.split("/").filter(Boolean);
    if (segs.length < 2) return null; // M3.4d: top-level pages never inherit.
    return `${dom}|${segs.slice(0, -1).join("/")}`;
  } catch {
    return null;
  }
}

function selfKey(url: string): string | null {
  try {
    const parsed = new URL(url.split("#")[0]);
    const dom = parsed.host.replace(/^www\./, "");
    const segs = parsed.pathname.split("/").filter(Boolean);
    return segs.length ? `${dom}|${segs.join("/")}` : null;
  } catch {
    return null;
  }
}

// ── Layer 3 — corpus-aware parent-path inheritance ──────────────────────────
export function enrichPageCategories(pages: PageRow[], log: LogFn = console.log): PageRow[] {
  if (!pages.length) return pages;

  // Dominant real category per named parent path.
  const byParent = new Map<string, Map<string, number>>();
  for (const p of pages) {
    const k = parentKey(p.page);
    if (k === null || !REAL_CATEGORIES.has(p.page_category)) continue;
    if (!byParent.has(k)) byParent.set(k, new Map());
    const m = byParent.get(k)!;
    m.set(p.page_category, (m.get(p.page_category) ?? 0) + 1);
  }
  const parentCat = new Map<string, string>();
  for (const [k, m] of byParent) {
    let best = "";
    let bestN = -1;
    for (const [cat, n] of m) if (n > bestN) { best = cat; bestN = n; }
    parentCat.set(k, best);
  }

  const before = pages.filter((p) => p.page_category === "Other").length;
  for (const p of pages) {
    if (p.page_category !== "Other") continue;
    const k = parentKey(p.page);
    if (k !== null && parentCat.has(k)) p.page_category = parentCat.get(k)!;
  }
  const after = pages.filter((p) => p.page_category === "Other").length;
  if (before > after) log(`▸ Category enrichment: ${before - after} 'Other' pages inherited a parent category.`);
  return pages;
}

// ── Hub detection (data-driven mechanism #1) ────────────────────────────────
export function detectHubPages(pages: PageRow[], log: LogFn = console.log): PageRow[] {
  if (!pages.length) return pages;
  const parentSet = new Set<string>();
  for (const p of pages) {
    const k = parentKey(p.page);
    if (k !== null) parentSet.add(k);
  }
  let n = 0;
  for (const p of pages) {
    const sk = selfKey(p.page);
    const isHub = sk !== null && parentSet.has(sk);
    // Do not override Contact / Other (Programmatic) flags already set.
    if (isHub && p.page_category !== "Contact" && p.page_category !== "Other") {
      p.page_category = "Hub";
      n++;
    }
  }
  if (n > 0) log(`▸ Hub pages: ${n} section index pages flagged (links to other pages, not standalone content).`);
  return pages;
}

// ── Programmatic series exclusion (KEPT, not dropped) ───────────────────────
export function detectProgrammaticSeries(pages: PageRow[], log: LogFn = console.log): PageRow[] {
  if (!pages.length) return pages;
  const counts = new Map<string, number>();
  for (const p of pages) {
    if (p.page_category !== "Other") continue;
    const k = parentKey(p.page);
    if (k !== null) counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  const flagged = new Map<string, number>();
  for (const [k, c] of counts) if (c >= PROGRAMMATIC_SERIES_MIN) flagged.set(k, c);
  if (!flagged.size) return pages;

  let n = 0;
  for (const p of pages) {
    if (p.page_category !== "Other") continue;
    const k = parentKey(p.page);
    if (k !== null && flagged.has(k)) {
      p.page_category = "Other"; // surfaced for review with no special label
      n++;
    }
  }
  log(`▸ Programmatic series: ${flagged.size} pattern(s), ${n} subfolder children re-tagged as 'Other'.`);
  const top = [...flagged.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  for (const [k, c] of top) {
    const [dom, parent] = k.split("|");
    log(`   • /${parent}/ on ${dom} (${c} pages)`);
  }
  return pages;
}

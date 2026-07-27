// ─────────────────────────────────────────────────────────────────────────────
// crawl.ts  —  Port of the metadata crawl block (CELL 4)
//
// Level 0 structural filter (drops Contact/Homepage/Hub + section-hub slugs) then
// a universal, cache-first crawl. Live fetch fans out at 32-way concurrency with a
// 3.0s timeout, mirroring the notebook's ThreadPoolExecutor. BeautifulSoup+lxml is
// replaced by cheerio; the metadata cache is read/written via BigQuery (bigquery.ts).
//
// FIX (M3.4.1): Level 0 filter now calls shouldExcludePage() from classify.ts
// instead of maintaining its own inline regex + category check. This ensures the
// crawl gate and the quickmatch gate are identical — both call the same function.
// ─────────────────────────────────────────────────────────────────────────────
import * as cheerio from "cheerio";
import { USE_BIGQUERY } from "./config";
import { PageRow, MetaRow, LogFn, urlHost, mapWithConcurrency } from "./util";
import { readMetaFromBigQuery, syncMetaToBigQuery } from "./bigquery";
import { shouldExcludePage } from "./classify";

const METADATA_HEADERS: Record<string, string> = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-US,en;q=0.5",
};

// Vocabulary-driven hub mechanism #2 — tests only the terminal path segment.
// Kept for logging/informational purposes; exclusion is now unified in
// shouldExcludePage() which covers this and more.
const SECTION_HUB_NOUNS =
  /\b(blog|blogs|guide|guides|resource|resources|article|articles|post|posts|news|insight|insights|learn|library|tip|tips|update|updates)\b/i;

export function isSectionHubUrl(url: string): boolean {
  try {
    const path = new URL(url.split("#")[0]).pathname.replace(/\/+$/, "");
    const terminal = path ? path.split("/").pop()! : "";
    return SECTION_HUB_NOUNS.test(terminal);
  } catch {
    return false;
  }
}

const EMPTY_META = (page: string): MetaRow => ({
  page, meta_title: "", meta_description: "", h1: "", h2: "",
});

async function fetchSinglePageMetadata(url: string, timeoutMs = 3000): Promise<MetaRow> {
  const clean = url.split("#")[0]; // strip client-side anchor fragment (M3.4d)
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(clean, {
      headers: METADATA_HEADERS,
      redirect: "follow",
      signal: ctrl.signal,
    });
    if (resp.status !== 200) return EMPTY_META(url);
    const ctype = resp.headers.get("content-type") ?? "";
    if (!ctype.toLowerCase().includes("html")) return EMPTY_META(url);

    const html = await resp.text();
    const $ = cheerio.load(html);
    const title = $("title").first().text().trim();
    const metaDesc = ($('meta[name="description" i]').attr("content") ?? "").trim();
    const h1 = $("h1").map((_, e) => $(e).text().trim()).get().filter(Boolean).join(" | ");
    const h2 = $("h2").map((_, e) => $(e).text().trim()).get().filter(Boolean).join(" | ");
    return { page: url, meta_title: title, meta_description: metaDesc, h1, h2 };
  } catch {
    return EMPTY_META(url);
  } finally {
    clearTimeout(t);
  }
}

export async function crawlMetadataForPages(pages: PageRow[], log: LogFn = console.log): Promise<MetaRow[]> {
  if (!pages.length) return [];

  // ── Level 0 filter ─────────────────────────────────────────────────────────
  // Uses the unified shouldExcludePage() gate (classify.ts) so this filter is
  // identical to the quickmatch working filter — no page slips through one but
  // not the other.
  const validSet = new Set<string>();
  for (const row of pages) {
    if (shouldExcludePage(row.page, row.page_category)) continue;
    validSet.add(row.page);
  }
  const validPages = [...validSet];
  const totalUnique = new Set(pages.map((p) => p.page)).size;
  log(`▸ Level 0 Filter: Dropped ${totalUnique - validPages.length} structural dead-ends/hubs. Evaluating ${validPages.length} viable URLs.`);

  // Group by domain for cache lookups.
  const byDomain = new Map<string, string[]>();
  for (const p of validPages) {
    const dom = urlHost(p);
    if (!byDomain.has(dom)) byDomain.set(dom, []);
    byDomain.get(dom)!.push(p);
  }

  const allFrames: MetaRow[] = [];
  const toCrawl: string[] = [];

  // BigQuery warehouse read (single batched lookup across all pages).
  let cachedByPage = new Map<string, MetaRow>();
  if (USE_BIGQUERY) {
    try {
      const cached = await readMetaFromBigQuery(validPages);
      cachedByPage = new Map(cached.map((c) => [c.page, c]));
      if (cached.length) log(`✓ Loaded ${cached.length} pages from the metadata warehouse.`);
    } catch (e) {
      log(`! Metadata warehouse read skipped: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  for (const [, urls] of byDomain) {
    for (const u of urls) {
      const c = cachedByPage.get(u);
      if (c) allFrames.push(c);
      else toCrawl.push(u);
    }
  }

  if (toCrawl.length === 0) return allFrames;
  log(`▸ Live Crawl: Fetching ${toCrawl.length} un-cached pages (Timeout=3.0s, Threads=32)...`);

  let done = 0;
  const fresh = await mapWithConcurrency(toCrawl, 32, async (url) => {
    const meta = await fetchSinglePageMetadata(url, 3000);
    done++;
    if (done % 50 === 0 || done === toCrawl.length) log(`  ... Crawled ${done}/${toCrawl.length}`);
    return meta;
  });

  // Write fresh rows back to the metadata warehouse.
  if (USE_BIGQUERY && fresh.length) await syncMetaToBigQuery(fresh, log);

  allFrames.push(...fresh);
  return allFrames;
}

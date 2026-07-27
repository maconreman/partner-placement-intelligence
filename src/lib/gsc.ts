// ─────────────────────────────────────────────────────────────────────────────
// gsc.ts  —  Port of GSC listing + fetch_all_domains() (CELL 2/4)
//
// Cache-first multi-domain fetch: read 7-day Sheets cache, probe live volume,
// chunk the heavy (>=25k row) domains by 30-day windows, aggregate, cache-write.
// Account→domain mapping is built from the listing so the data@ / analytics@
// split is honored per property.
// ─────────────────────────────────────────────────────────────────────────────
import {
  GSC_ACCOUNT_EMAILS, FFG_OWNED_DOMAINS, GSC_ROW_LIMIT, GSC_MAX_WORKERS,
  USE_BIGQUERY,
} from "./config";
import { GscRow, LogFn, mapWithConcurrency, sleep } from "./util";
import { gscClient } from "./googleAuth";
import { fetchFromBigQuery, syncToBigQuery } from "./bigquery";

const domainAccountMap = new Map<string, string>();

function accountForDomain(domain: string): string {
  return domainAccountMap.get(domain) ?? "data";
}

export interface PropsResult {
  propsByAccount: Record<string, string[]>;
  ordered: string[];
}

export async function listGscProperties(): Promise<PropsResult> {
  const propsByAccount: Record<string, string[]> = {};
  const ffgSet = new Set(FFG_OWNED_DOMAINS);

  for (const key of Object.keys(GSC_ACCOUNT_EMAILS)) {
    try {
      const client = await gscClient(key);
      const resp = await client.sites.list();
      const entries = resp.data.siteEntry ?? [];
      propsByAccount[key] = entries
        .map((s) => s.siteUrl ?? "")
        .filter((u) => u.startsWith("sc-domain:"))
        .sort();
    } catch (e) {
      console.error(`GSC error [${key}]:`, e instanceof Error ? e.message : e);
      propsByAccount[key] = [];
    }
  }

  domainAccountMap.clear();
  for (const [key, domains] of Object.entries(propsByAccount)) {
    for (const d of domains) domainAccountMap.set(d, key);
  }

  const all = new Set<string>();
  for (const domains of Object.values(propsByAccount)) for (const d of domains) all.add(d);
  const ffgOrdered = FFG_OWNED_DOMAINS.filter((d) => all.has(d));
  const clientOrder = [...all].filter((d) => !ffgSet.has(d)).sort();
  return { propsByAccount, ordered: [...ffgOrdered, ...clientOrder] };
}

async function executeWithBackoff(client: Awaited<ReturnType<typeof gscClient>>, siteUrl: string, body: any, maxRetries = 5) {
  for (let n = 0; n < maxRetries; n++) {
    try {
      const resp = await client.searchanalytics.query({ siteUrl, requestBody: body });
      return resp.data;
    } catch (e: any) {
      const status = e?.code ?? e?.response?.status;
      if ([429, 500, 502, 503, 504].includes(status)) {
        await sleep((2 ** n) * 1000 + Math.random() * 1000);
      } else {
        return null;
      }
    }
  }
  return null;
}

function rowsFromResp(account: string, domain: string, data: any): GscRow[] {
  const rows = data?.rows ?? [];
  return rows.map((r: any) => ({
    account, domain,
    query: r.keys?.[0] ?? "",
    page: r.keys?.[1] ?? "",
    clicks: Math.round(r.clicks ?? 0),
    impressions: Math.round(r.impressions ?? 0),
    position: Number(r.position ?? 0),
  }));
}

async function probeDomain(domain: string, startDate: string, endDate: string): Promise<GscRow[]> {
  const key = accountForDomain(domain);
  const client = await gscClient(key);
  const body = { startDate, endDate, dimensions: ["query", "page"], rowLimit: 25000, startRow: 0 };
  const data = await executeWithBackoff(client, domain, body);
  return data ? rowsFromResp(key, domain, data) : [];
}

async function fetchDomainChunk(domain: string, cStart: string, cEnd: string): Promise<GscRow[]> {
  const key = accountForDomain(domain);
  const client = await gscClient(key);
  const all: GscRow[] = [];
  const body: any = { startDate: cStart, endDate: cEnd, dimensions: ["query", "page"], rowLimit: 25000, startRow: 0 };
  while (true) {
    const data = await executeWithBackoff(client, domain, body);
    if (!data) break;
    const batch = rowsFromResp(key, domain, data);
    if (!batch.length) break;
    all.push(...batch);
    if (batch.length < 25000) break;
    body.startRow += batch.length;
  }
  return all;
}

// Aggregate raw query/page rows for a heavy domain (impression-weighted position).
function aggregateRaw(rows: GscRow[]): GscRow[] {
  const groups = new Map<string, { account: string; domain: string; query: string; page: string; clicks: number; impressions: number; pos_x_imp: number }>();
  for (const r of rows) {
    const k = `${r.account}|${r.domain}|${r.query}|${r.page}`;
    let g = groups.get(k);
    if (!g) {
      g = { account: r.account, domain: r.domain, query: r.query, page: r.page, clicks: 0, impressions: 0, pos_x_imp: 0 };
      groups.set(k, g);
    }
    g.clicks += r.clicks;
    g.impressions += r.impressions;
    g.pos_x_imp += r.position * r.impressions;
  }
  const out: GscRow[] = [];
  for (const g of groups.values()) {
    out.push({
      account: g.account, domain: g.domain, query: g.query, page: g.page,
      clicks: g.clicks, impressions: g.impressions,
      position: g.impressions > 0 ? Math.round((g.pos_x_imp / g.impressions) * 100) / 100 : 0.0,
    });
  }
  out.sort((a, b) => (b.clicks - a.clicks) || (b.impressions - a.impressions));
  return out.slice(0, GSC_ROW_LIMIT);
}

function dateChunks(startDate: string, endDate: string): Array<[string, string]> {
  const chunks: Array<[string, string]> = [];
  let current = new Date(startDate);
  const end = new Date(endDate);
  while (current <= end) {
    const chunkEnd = new Date(current);
    chunkEnd.setDate(chunkEnd.getDate() + 29);
    const ce = chunkEnd > end ? end : chunkEnd;
    chunks.push([current.toISOString().slice(0, 10), ce.toISOString().slice(0, 10)]);
    current = new Date(ce);
    current.setDate(current.getDate() + 1);
  }
  return chunks;
}

export async function fetchAllDomains(
  domains: string[],
  startDate: string,
  endDate: string,
  log: LogFn = console.log
): Promise<GscRow[]> {
  // ── Primary path: BigQuery warehouse (when provisioned) ───────────────────
  // A single batched read covers every requested domain and date range. If the
  // warehouse has any rows, they are used directly — no freshness gate. Only
  // falls through to live GSC when the warehouse returns zero rows (first run
  // before any data is synced, or a domain/date range not yet warmed up).
  if (USE_BIGQUERY) {
    try {
      log("▸ Reading from the BigQuery warehouse...");
      const { rows } = await fetchFromBigQuery(domains, startDate, endDate, log);
      if (rows.length) return rows;
      log("! Warehouse empty for this date range — fetching live from Search Console.");
    } catch (e) {
      log(`! BigQuery unavailable (${e instanceof Error ? e.message : String(e)}) — fetching live from Search Console.`);
    }
  }

  // ── Live GSC fetch (warehouse miss, or BigQuery not provisioned) ──────────
  log("▸ Connecting to sites and checking search traffic volumes...");
  const finalFrames: GscRow[] = [];
  const liveDomains: string[] = [...domains];

  const liveFrames = new Map<string, GscRow[]>();
  const heavyDomains: string[] = [];

  await mapWithConcurrency(liveDomains, GSC_MAX_WORKERS, async (d) => {
    const short = d.replace("sc-domain:", "");
    try {
      const probe = await probeDomain(d, startDate, endDate);
      if (!probe.length) {
        log(`! ${short} — No search data found for these dates.`);
      } else if (probe.length >= 25000) {
        log(`▸ ${short} — Reading high-volume history (this may take a moment)...`);
        heavyDomains.push(d);
      } else {
        log(`✓ ${short} — Successfully scanned.`);
        liveFrames.set(d, probe);
      }
    } catch (e) {
      log(`! ${short} — Error: ${e instanceof Error ? e.message : String(e)}`);
    }
  });

  if (heavyDomains.length) {
    const chunks = dateChunks(startDate, endDate);
    const tasks = heavyDomains.flatMap((d) => chunks.map(([cs, ce]) => ({ d, cs, ce })));
    const raw = new Map<string, GscRow[]>();
    for (const d of heavyDomains) raw.set(d, []);

    await mapWithConcurrency(tasks, GSC_MAX_WORKERS, async ({ d, cs, ce }) => {
      try {
        const chunk = await fetchDomainChunk(d, cs, ce);
        if (chunk.length) raw.get(d)!.push(...chunk);
      } catch {
        /* swallow chunk errors */
      }
    });

    for (const d of heavyDomains) {
      const short = d.replace("sc-domain:", "");
      const list = raw.get(d)!;
      if (!list.length) {
        log(`! ${short} — No search data found for these dates.`);
      } else {
        liveFrames.set(d, aggregateRaw(list));
        log(`✓ ${short} — Successfully scanned.`);
      }
    }
  }

  const freshlyFetched: GscRow[] = [];
  for (const [, frame] of liveFrames) {
    finalFrames.push(...frame);
    freshlyFetched.push(...frame);
  }

  // Persist the fresh rows to the warehouse so the next run reads from BigQuery.
  if (USE_BIGQUERY && freshlyFetched.length) {
    await syncToBigQuery(freshlyFetched, log);
  }
  return finalFrames;
}

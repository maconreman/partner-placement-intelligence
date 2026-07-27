// ─────────────────────────────────────────────────────────────────────────────
// bigquery.ts  —  Warehouse read path (Beta primary source)
//
// When the warehouse is provisioned (USE_BIGQUERY === true), fetchAllDomains()
// reads aggregated GSC rows from here FIRST. Any failure — unconfigured, auth
// error, empty result — falls back to the Google Sheets cache → live GSC path,
// so a missing or cold warehouse never breaks a run.
//
// Auth: a GCP service-account JSON string in GCP_SERVICE_ACCOUNT_JSON. The
// OAuth user tokens used elsewhere authorize Search Console / Drive / Sheets,
// not BigQuery jobs, so the warehouse uses its own service-account identity.
//
// Schema expected in `${BQ_PROJECT_ID}.${BQ_DATASET_ID}.${BQ_TABLE_ID}`:
//   account STRING, domain STRING, query STRING, page STRING,
//   clicks INT64, impressions INT64, position FLOAT64, date DATE
// (this matches the column set GSC's native BigQuery bulk export produces after
//  a light view — see BIGQUERY_SETUP.md).
//
// FIX (M3.4.1): syncToBigQuery and syncMetaToBigQuery now chunk large payloads
// before calling table.insert(). BigQuery's streaming-insert API has a hard 10 MB
// per-request body limit and a 50,000 row per-request limit. A live 12-month GSC
// fetch across 13+ domains can produce tens of thousands of rows, which caused
// an HTTP 413 (Request Entity Too Large) and silently left the warehouse empty.
// Rows are now inserted in BQ_INSERT_CHUNK_SIZE batches (500 rows) so every
// insert stays well within both limits.
// ─────────────────────────────────────────────────────────────────────────────
import { BigQuery } from "@google-cloud/bigquery";
import { BQ_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_ID, BQ_META_TABLE_ID } from "./config";
import { GscRow, MetaRow, FeedbackRow, LogFn } from "./util";

// Safe batch size for streaming inserts: well under the 10 MB / 50k row limits.
const BQ_INSERT_CHUNK_SIZE = 500;

let cached: BigQuery | null = null;

function client(): BigQuery {
  if (cached) return cached;
  const raw = process.env.GCP_SERVICE_ACCOUNT_JSON ?? "";
  const opts: ConstructorParameters<typeof BigQuery>[0] = { projectId: BQ_PROJECT_ID };
  if (raw) {
    const creds = JSON.parse(raw) as { client_email: string; private_key: string; project_id?: string };
    opts.credentials = { client_email: creds.client_email, private_key: creds.private_key };
    if (creds.project_id) opts.projectId = creds.project_id;
  }
  cached = new BigQuery(opts);
  return cached;
}

function tableRef(): string {
  return `\`${BQ_PROJECT_ID}.${BQ_DATASET_ID}.${BQ_TABLE_ID}\``;
}

function weekMonday(): string {
  const d = new Date();
  const day = (d.getUTCDay() + 6) % 7; // Monday = 0
  d.setUTCDate(d.getUTCDate() - day);
  return d.toISOString().slice(0, 10);
}

// Splits an array into sequential chunks of at most `size` elements.
function chunkArray<T>(arr: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < arr.length; i += size) chunks.push(arr.slice(i, i + size));
  return chunks;
}

// PRIMARY read. Returns all rows matching the requested domains and date range.
// If the warehouse has data for the range, it is used directly — no freshness
// gate. Only falls through to live GSC when the warehouse returns zero rows,
// which handles the first-ever run before any data is synced.
export async function fetchFromBigQuery(
  domains: string[],
  startDate: string,
  endDate: string,
  log: LogFn = console.log
): Promise<{ rows: GscRow[] }> {
  const bq = client();
  const sql = `
    SELECT account, domain, query, page,
           SUM(clicks)       AS clicks,
           SUM(impressions)  AS impressions,
           SAFE_DIVIDE(SUM(position * impressions), SUM(impressions)) AS position,
    FROM ${tableRef()}
    WHERE domain IN UNNEST(@domains)
      AND date BETWEEN @startDate AND @endDate
    GROUP BY account, domain, query, page
  `;
  const [rows] = await bq.query({
    query: sql,
    params: { domains, startDate, endDate },
    types: { domains: ["STRING"], startDate: "DATE", endDate: "DATE" },
  });

  const raw = rows as Record<string, unknown>[];

  const out: GscRow[] = raw.map((r) => ({
    account: String(r.account ?? ""),
    domain: String(r.domain ?? ""),
    query: String(r.query ?? ""),
    page: String(r.page ?? ""),
    clicks: Math.round(Number(r.clicks ?? 0)),
    impressions: Math.round(Number(r.impressions ?? 0)),
    position: Number(r.position ?? 0),
  }));

  if (out.length) {
    log(`✓ Warehouse has ${out.length.toLocaleString()} rows for the requested date range.`);
  }
  return { rows: out };
}

// Best-effort warm-up. Appends freshly fetched live rows into the warehouse so
// the next run can read them from BigQuery. Tagged with the current week's
// Monday as `date`. Never throws — warm-up failure must not fail a run.
//
// FIX (M3.4.1): rows are inserted in BQ_INSERT_CHUNK_SIZE batches to avoid the
// HTTP 413 that occurred when the full live-fetch payload (potentially 50k+ rows)
// was sent as a single streaming-insert request.
export async function syncToBigQuery(rows: GscRow[], log: LogFn = console.log): Promise<void> {
  if (!rows.length) return;
  try {
    const bq = client();
    // bq.date() wraps the ISO string as a BigQuery DATE value. Without this
    // wrapper the Node.js streaming-insert API sends it as a plain string and
    // BigQuery silently skips every row (skipInvalidRows:true), logging success
    // but writing nothing. Metadata works because snapshot_date is not the
    // partition key and BigQuery is lenient; the DATE partition column on
    // gsc_data requires the typed wrapper.
    const date = bq.date(weekMonday());
    const payload = rows.map((r) => ({
      account: r.account, domain: r.domain, query: r.query, page: r.page,
      clicks: r.clicks, impressions: r.impressions, position: r.position, date,
    }));

    const chunks = chunkArray(payload, BQ_INSERT_CHUNK_SIZE);
    const table = bq.dataset(BQ_DATASET_ID).table(BQ_TABLE_ID);
    for (const chunk of chunks) {
      await table.insert(chunk, { ignoreUnknownValues: true, skipInvalidRows: true });
    }
    log(`✓ Warmed warehouse with ${rows.length.toLocaleString()} rows (${chunks.length} batch${chunks.length === 1 ? "" : "es"}).`);
  } catch (e) {
    log(`! Warehouse warm-up skipped: ${e instanceof Error ? e.message : String(e)}`);
  }
}

// ── Metadata warehouse (page_metadata) ──────────────────────────────────────
// Read cached page metadata for a set of URLs. Returns whatever the warehouse
// has; the caller crawls the misses and writes them back via syncMetaToBigQuery.
export async function readMetaFromBigQuery(pages: string[]): Promise<MetaRow[]> {
  if (!pages.length) return [];
  const bq = client();
  const sql = `
    SELECT page, meta_title, meta_description, h1, h2
    FROM \`${BQ_PROJECT_ID}.${BQ_DATASET_ID}.${BQ_META_TABLE_ID}\`
    WHERE page IN UNNEST(@pages)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY page ORDER BY snapshot_date DESC) = 1
  `;
  const [rows] = await bq.query({ query: sql, params: { pages }, types: { pages: ["STRING"] } });
  return (rows as Record<string, unknown>[]).map((r) => ({
    page: String(r.page ?? ""),
    meta_title: String(r.meta_title ?? ""),
    meta_description: String(r.meta_description ?? ""),
    h1: String(r.h1 ?? ""),
    h2: String(r.h2 ?? ""),
  }));
}

// Append freshly crawled metadata into the warehouse. Never throws.
//
// FIX (M3.4.1): rows are inserted in BQ_INSERT_CHUNK_SIZE batches to avoid 413
// errors on large crawl result sets.
export async function syncMetaToBigQuery(rows: MetaRow[], log: LogFn = console.log): Promise<void> {
  if (!rows.length) return;
  try {
    const bq = client();
    const snapshot_date = new Date().toISOString().slice(0, 10);
    const payload = rows.map((r) => ({
      snapshot_date,
      page: r.page,
      meta_title: r.meta_title,
      meta_description: r.meta_description,
      h1: r.h1,
      h2: r.h2,
    }));

    const chunks = chunkArray(payload, BQ_INSERT_CHUNK_SIZE);
    const table = bq.dataset(BQ_DATASET_ID).table(BQ_META_TABLE_ID);
    for (const chunk of chunks) {
      await table.insert(chunk, { ignoreUnknownValues: true, skipInvalidRows: true });
    }
    log(`✓ Saved ${rows.length.toLocaleString()} pages to the metadata warehouse (${chunks.length} batch${chunks.length === 1 ? "" : "es"}).`);
  } catch (e) {
    log(`! Metadata warehouse write skipped: ${e instanceof Error ? e.message : String(e)}`);
  }
}

// Sync a single feedback submission to BigQuery — the sole feedback store.
// Never throws.
import { BQ_FEEDBACK_TABLE_ID } from "./config";

export async function syncFeedbackToBigQuery(row: FeedbackRow): Promise<void> {
  if (!BQ_PROJECT_ID || !BQ_DATASET_ID) return;
  try {
    const bq = client();
    await bq.dataset(BQ_DATASET_ID).table(BQ_FEEDBACK_TABLE_ID).insert([{
      submitted_at: new Date().toISOString().slice(0, 10),
      query: row.query,
      vertical: row.vertical,
      category: row.category,
      topic: row.topic,
      domains: row.domains,
    }], { ignoreUnknownValues: true, skipInvalidRows: true });
  } catch {
    /* best-effort — never fail a submission */
  }
}

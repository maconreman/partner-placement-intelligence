"""
bigquery.py — BigQuery warehouse read/write.
Port of bigquery.ts. Preserves D11 (bq DATE wrapper), D12 (freshness gate),
D13 (no Sheets fallback).

M7 fix: _client() strips GCP_SERVICE_ACCOUNT_JSON before the truthiness check
and wraps json.loads in a specific JSONDecodeError handler, so a misconfigured
secret is distinguishable from BigQuery being unreachable.

M8.2 fixes, addressing "GSC last sync: Never" with an apparently successful sync:

1. ensure_tables() creates the dataset and all three tables when missing.
   Previously nothing created them. On a fresh project the first insert failed
   with NotFound, the exception was swallowed by the best-effort handler, and
   the run reported success while writing nothing.

2. The GSC snapshot is written with a load job into a partition decorator
   (table$YYYYMMDD) using WRITE_TRUNCATE, replacing the previous streaming
   insert plus DELETE. Three reasons:
     a. Streamed rows sit in a write-optimized buffer for up to 90 minutes and
        cannot be deleted, so the M8 delete-before-insert failed on any re-run
        inside that window, which is exactly when a retry happens.
     b. WRITE_TRUNCATE on one partition is atomic, so idempotency no longer
        depends on a separate delete succeeding first.
     c. Load jobs are free. Streaming inserts are billed per byte.

3. IMPORTANT, and it reverses a previous rule: load jobs take DATE values as
   ISO strings in the JSON payload, not as datetime.date objects. D11 (native
   date object required) applies to the streaming-insert path, which the GSC
   snapshot no longer uses. Passing a date object to a load job raises
   "Object of type date is not JSON serializable". Do not "restore" D11 here.
   DECISIONS.md needs updating to scope D11 to streaming inserts only.

4. bq.dataset(...).table(...) replaced with explicit TableReference. The old
   form is deprecated in google-cloud-bigquery 3.x and raises on some versions.

5. get_last_sync() backs the /admin status card, which is what renders "Never".

M9.1 fix (D-M9-6) — the warehouse read must not apply a lag-adjusted upper bound:

   fetch_from_bigquery() previously filtered `date BETWEEN @startDate AND
   @endDate`. Callers build that window with _date_range(), where
   end_date = today - GSC_LAG_DAYS. But sync_to_bigquery() stamps every
   snapshot with THIS WEEK'S MONDAY. Early in the week the snapshot date is
   newer than the reader's own end_date, so the row was discarded by the WHERE
   clause before QUALIFY or the freshness check ever saw it.

   Observed: a snapshot dated 2026-07-27 (Monday) with 25,000 rows for
   sc-domain:fundly.com, read on Tuesday 2026-07-28 with end_date 2026-07-25.
   COUNT(*) showed the rows; the read reported "Warehouse empty" and fell
   through to a full live fetch. This recurs every week the sync lands inside
   the lag window, which is most of them.

   GSC_LAG_DAYS exists to stop us asking Google for data that has not settled.
   That is a live-fetch concern and it stays in _live_gsc_fetch. It has no
   meaning for a warehouse read, where "which rows do we want" is already
   answered by QUALIFY date = MAX(date) OVER (PARTITION BY domain), and
   "are they recent enough" is already answered by the freshness check below
   the query. The upper bound was duplicating that job and getting it wrong.

   The lower bound is kept: it prunes partitions (the table is DAY-partitioned
   on `date`, so this is what keeps the read cheap) and it cannot exclude a
   current snapshot, since this week's Monday is always newer than
   today - 365 days.
"""
from __future__ import annotations
import os
import json
from datetime import date, timedelta
from typing import Optional
from google.cloud import bigquery
from google.oauth2 import service_account

from .config import BQ_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_ID, BQ_META_TABLE_ID, BQ_FEEDBACK_TABLE_ID
from .util import GscRow, MetaRow, FeedbackRow, LogFn

_bq_client: Optional[bigquery.Client] = None

# Only the single-row feedback write still uses a streaming insert.
BQ_CHUNK_SIZE = 500

# M7: When BQ_STRICT_WRITES is truthy, any streaming-insert error is raised
# instead of swallowed. This surfaces silent row drops during testing — the
# exact failure mode that historically made the GSC warehouse appear empty.
# Leave unset in production so a warehouse hiccup never fails a user's run.
BQ_STRICT_WRITES = os.environ.get("BQ_STRICT_WRITES", "").lower() in ("1", "true", "yes")


def _client() -> bigquery.Client:
    global _bq_client
    if _bq_client:
        return _bq_client
    # M7 fix: strip whitespace/newlines before the truthiness check.
    # GCP_SERVICE_ACCOUNT_JSON is often pasted into HF Spaces secrets and may
    # contain a trailing newline. "\n".strip() == "" (falsy) so we skip the
    # parse and fall through to ADC. A non-empty but malformed value raises a
    # clear JSONDecodeError rather than the generic "Expecting value" message
    # that previously made a bad secret look like BigQuery being unreachable.
    raw = os.environ.get("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"GCP_SERVICE_ACCOUNT_JSON is set but cannot be parsed as JSON: {exc}. "
                "Check that the secret was pasted correctly (no extra quotes, no truncation)."
            ) from exc
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        _bq_client = bigquery.Client(project=BQ_PROJECT_ID, credentials=creds)
    else:
        _bq_client = bigquery.Client(project=BQ_PROJECT_ID)
    return _bq_client


# ── Table schemas ─────────────────────────────────────────────────────────────
# Declared here so ensure_tables() can create anything missing. The GSC table is
# partitioned on `date`, which is what makes the partition-decorator overwrite
# in sync_to_bigquery() possible.
_GSC_SCHEMA = [
    bigquery.SchemaField("account", "STRING"),
    bigquery.SchemaField("domain", "STRING"),
    bigquery.SchemaField("query", "STRING"),
    bigquery.SchemaField("page", "STRING"),
    bigquery.SchemaField("clicks", "INTEGER"),
    bigquery.SchemaField("impressions", "INTEGER"),
    bigquery.SchemaField("position", "FLOAT"),
    bigquery.SchemaField("date", "DATE"),
]

_META_SCHEMA = [
    bigquery.SchemaField("snapshot_date", "DATE"),
    bigquery.SchemaField("page", "STRING"),
    bigquery.SchemaField("meta_title", "STRING"),
    bigquery.SchemaField("meta_description", "STRING"),
    bigquery.SchemaField("h1", "STRING"),
    bigquery.SchemaField("h2", "STRING"),
]

_FEEDBACK_SCHEMA = [
    bigquery.SchemaField("submitted_at", "DATE"),
    bigquery.SchemaField("query", "STRING"),
    bigquery.SchemaField("vertical", "STRING"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("topic", "STRING"),
    bigquery.SchemaField("domains", "STRING"),
]

_ensured = False


def _tref(table_id: str) -> bigquery.TableReference:
    """Explicit TableReference. Replaces the deprecated bq.dataset().table()."""
    return bigquery.TableReference(
        bigquery.DatasetReference(BQ_PROJECT_ID, BQ_DATASET_ID), table_id
    )


def ensure_tables(log: LogFn = print) -> None:
    """
    Create the dataset and any missing tables. Idempotent and cached per process.

    Called at the start of every admin and cron sync. Without this, a fresh GCP
    project fails its first insert with NotFound, the best-effort handler
    swallows it, and the admin card keeps reading "Never" while the sync log
    claims success.

    Raises on failure. A sync that cannot guarantee its destination should stop
    rather than continue and report success.
    """
    global _ensured
    if _ensured:
        return
    if not BQ_PROJECT_ID or not BQ_DATASET_ID:
        raise RuntimeError(
            "BQ_PROJECT_ID and BQ_DATASET_ID must be set. On Hugging Face these "
            "live in the Space's Settings > Variables and secrets, not in .env."
        )
    bq = _client()

    ds_ref = bigquery.DatasetReference(BQ_PROJECT_ID, BQ_DATASET_ID)
    try:
        bq.get_dataset(ds_ref)
    except Exception:
        ds = bigquery.Dataset(ds_ref)
        ds.location = os.environ.get("BQ_LOCATION", "US")
        bq.create_dataset(ds, exists_ok=True)
        log(f"Created dataset {BQ_PROJECT_ID}.{BQ_DATASET_ID}.")

    plan = [
        (BQ_TABLE_ID, _GSC_SCHEMA, "date"),
        (BQ_META_TABLE_ID, _META_SCHEMA, "snapshot_date"),
        (BQ_FEEDBACK_TABLE_ID, _FEEDBACK_SCHEMA, None),
    ]
    for table_id, schema, partition_field in plan:
        if not table_id:
            continue
        ref = _tref(table_id)
        try:
            bq.get_table(ref)
            continue
        except Exception:
            pass
        table = bigquery.Table(ref, schema=schema)
        if partition_field:
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY, field=partition_field
            )
        bq.create_table(table, exists_ok=True)
        log(f"Created table {BQ_DATASET_ID}.{table_id}.")

    _ensured = True


def get_last_sync() -> dict:
    """
    Newest snapshot date in the GSC and metadata tables, plus row counts.

    Backs the /admin status card. Returns None dates when a table is absent or
    empty, which is what the card renders as "Never". Distinguishing absent from
    empty matters: absent means ensure_tables() never ran, empty means the sync
    ran and wrote nothing.
    """
    bq = _client()
    out: dict = {
        "gsc_last_sync": None, "gsc_rows": 0, "gsc_table_exists": False,
        "meta_last_sync": None, "meta_rows": 0, "meta_table_exists": False,
    }
    for key, table_id, date_col in (
        ("gsc", BQ_TABLE_ID, "date"),
        ("meta", BQ_META_TABLE_ID, "snapshot_date"),
    ):
        if not table_id:
            continue
        try:
            bq.get_table(_tref(table_id))
        except Exception:
            continue
        out[f"{key}_table_exists"] = True
        sql = (
            f"SELECT MAX({date_col}) AS max_date, COUNT(*) AS n "
            f"FROM `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{table_id}`"
        )
        row = next(iter(bq.query(sql).result()), None)
        if row and row.max_date:
            out[f"{key}_last_sync"] = str(row.max_date)
        out[f"{key}_rows"] = int(row.n) if row and row.n else 0
    return out


def _week_monday() -> str:
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()


def _table_ref() -> str:
    return f"`{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_TABLE_ID}`"


# ── GSC data ──────────────────────────────────────────────────────────────────

async def fetch_from_bigquery(
    domains: list[str],
    start_date: str,
    end_date: str,
    log: LogFn = print,
) -> tuple[list[GscRow], bool]:
    """
    Primary read. Returns (rows, fresh) where fresh=True when newest date >= this
    week's Monday. Callers fall through to live GSC fetch on empty or stale.

    `end_date` is accepted for signature compatibility with the live-fetch
    callers but is deliberately NOT used as an upper bound on `date` — see
    D-M9-6 and the note in the module docstring. Applying it discarded the
    current week's snapshot whenever the sync landed inside the GSC lag window.
    """
    bq = _client()
    # M8 fix: read only each domain's LATEST snapshot, never sum across
    # snapshots. Each sync writes a full 365-day GSC window stamped with that
    # week's Monday as the partition date. The old query SUMmed clicks and
    # impressions across every snapshot in range, so week N inflated metrics
    # ~N-fold once weekly scheduled syncs began. QUALIFY pins each domain to
    # its own newest snapshot (per-domain, not global, so one lagging domain
    # neither disappears nor drags the others). GROUP BY then collapses only
    # duplicate rows *within* one snapshot (e.g. a retried write) via MAX,
    # which is identity on identical duplicates — not SUM.
    #
    # M9.1 fix (D-M9-6): the upper bound `AND date <= @endDate` is GONE.
    # Callers pass end_date = today - GSC_LAG_DAYS, while sync_to_bigquery()
    # stamps snapshots with this week's Monday. Early in the week the snapshot
    # is NEWER than end_date, so the WHERE clause dropped it before QUALIFY ran
    # and the read reported an empty warehouse over a table that plainly had
    # rows. Selecting the right snapshot is QUALIFY's job; judging whether it is
    # recent enough is the freshness check below. Neither needs an upper bound.
    #
    # The lower bound stays: it prunes partitions on this DAY-partitioned table
    # (keeping the read cheap) and can never exclude a live snapshot, because
    # this week's Monday is always newer than today - 365 days.
    sql = f"""
        SELECT account, domain, query, page,
               MAX(clicks)       AS clicks,
               MAX(impressions)  AS impressions,
               SAFE_DIVIDE(SUM(position * impressions), SUM(impressions)) AS position,
               MAX(date)         AS max_date
        FROM (
            SELECT account, domain, query, page, clicks, impressions, position, date
            FROM {_table_ref()}
            WHERE domain IN UNNEST(@domains)
              AND date >= @startDate
            QUALIFY date = MAX(date) OVER (PARTITION BY domain)
        )
        GROUP BY account, domain, query, page
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("domains", "STRING", domains),
            bigquery.ScalarQueryParameter("startDate", "DATE", start_date),
        ]
    )
    rows = list(bq.query(sql, job_config=job_config).result())

    out: list[GscRow] = []
    max_date = ""
    # M8: freshness is judged on the OLDEST per-domain snapshot among the
    # domains that have rows. With per-domain latest reads, a globally-fresh
    # max would mask one lagging domain serving stale data; if any present
    # domain's snapshot predates this week's Monday, the whole read is stale
    # and the caller refreshes live (D12 short-circuits reads only).
    domain_max: dict[str, str] = {}
    for r in rows:
        d = str(r.max_date) if r.max_date else ""
        if d > max_date:
            max_date = d
        dom = str(r.domain or "")
        if d > domain_max.get(dom, ""):
            domain_max[dom] = d
        out.append(GscRow(
            account=str(r.account or ""),
            domain=str(r.domain or ""),
            query=str(r.query or ""),
            page=str(r.page or ""),
            clicks=int(r.clicks or 0),
            impressions=int(r.impressions or 0),
            position=float(r.position or 0),
        ))

    oldest_domain_snapshot = min(domain_max.values()) if domain_max else ""
    fresh = bool(out and oldest_domain_snapshot and oldest_domain_snapshot >= _week_monday())
    if out:
        log(f"Warehouse has {len(out):,} rows (newest {max_date or 'n/a'}, {'fresh' if fresh else 'stale'}).")
    return out, fresh


async def sync_to_bigquery(rows: list[GscRow], log: LogFn = print) -> None:
    """
    Write the weekly GSC snapshot, replacing this week's partition atomically.

    Uses a load job into the partition decorator table$YYYYMMDD with
    WRITE_TRUNCATE. Re-running in the same week replaces the snapshot rather
    than appending, so manual re-syncs, Cloud Scheduler retries and accidental
    double triggers are all idempotent by construction.

    This replaces the earlier streaming insert plus DELETE. Streamed rows are
    undeletable for up to 90 minutes, so the delete failed precisely when a
    retry needed it. Load jobs are also free, where streaming inserts are billed.

    Note on date typing, which reverses the older streaming rule: load jobs
    serialize the payload as JSON, so the DATE column takes an ISO string.
    Passing a datetime.date object here raises "Object of type date is not JSON
    serializable". D11's native-date requirement applies to insert_rows_json,
    not to this path.

    Best-effort unless BQ_STRICT_WRITES is set, in which case failures raise so
    they surface during testing instead of being swallowed.
    """
    if not rows:
        log("No rows to write, skipping warehouse write.")
        return
    try:
        ensure_tables(log)
        bq = _client()
        monday = date.fromisoformat(_week_monday())
        payload = [
            {
                "account": r.account, "domain": r.domain, "query": r.query,
                "page": r.page, "clicks": r.clicks, "impressions": r.impressions,
                "position": r.position, "date": monday.isoformat(),
            }
            for r in rows
        ]
        target = f"{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_TABLE_ID}${monday.strftime('%Y%m%d')}"
        job_config = bigquery.LoadJobConfig(
            schema=_GSC_SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        job = bq.load_table_from_json(payload, target, job_config=job_config)
        job.result()
        if job.errors:
            raise RuntimeError(f"Load job reported errors: {job.errors[:2]}")

        # Confirm from the table itself rather than trusting the job status.
        # A silently empty warehouse is the failure this path exists to prevent,
        # so the log line reflects what is actually stored.
        verify_sql = (
            f"SELECT COUNT(*) AS n FROM `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_TABLE_ID}` "
            "WHERE date = @snapshotDate"
        )
        verify_cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("snapshotDate", "DATE", monday)]
        )
        stored = next(iter(bq.query(verify_sql, job_config=verify_cfg).result()), None)
        n = int(stored.n) if stored and stored.n else 0
        if n == 0:
            raise RuntimeError(
                f"Load job completed but the {monday} partition is empty. Check "
                "that the service account has BigQuery Data Editor on "
                f"{BQ_PROJECT_ID}.{BQ_DATASET_ID}."
            )
        log(f"Warmed warehouse with {n:,} rows for snapshot {monday}.")
    except Exception as e:
        if BQ_STRICT_WRITES:
            raise
        log(f"Warehouse write failed: {e}")


# ── Metadata ──────────────────────────────────────────────────────────────────

async def read_meta_from_bigquery(pages: list[str]) -> list[MetaRow]:
    if not pages:
        return []
    bq = _client()
    sql = f"""
        SELECT page, meta_title, meta_description, h1, h2
        FROM `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_META_TABLE_ID}`
        WHERE page IN UNNEST(@pages)
        QUALIFY ROW_NUMBER() OVER (PARTITION BY page ORDER BY snapshot_date DESC) = 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter("pages", "STRING", pages)]
    )
    rows = list(bq.query(sql, job_config=job_config).result())
    return [
        MetaRow(
            page=str(r.page or ""),
            meta_title=str(r.meta_title or ""),
            meta_description=str(r.meta_description or ""),
            h1=str(r.h1 or ""),
            h2=str(r.h2 or ""),
        )
        for r in rows
    ]


async def sync_meta_to_bigquery(rows: list[MetaRow], log: LogFn = print) -> None:
    """
    Append today's page metadata snapshot.

    Appends rather than overwrites because read_meta_from_bigquery() takes the
    newest row per page, so older snapshots are harmless history. Uses a load
    job for the same reasons as the GSC write: free, and no streaming buffer.
    """
    if not rows:
        return
    try:
        ensure_tables(log)
        bq = _client()
        today = date.today().isoformat()
        payload = [
            {
                "snapshot_date": today,
                "page": r.page, "meta_title": r.meta_title,
                "meta_description": r.meta_description,
                "h1": r.h1, "h2": r.h2,
            }
            for r in rows
        ]
        job_config = bigquery.LoadJobConfig(
            schema=_META_SCHEMA,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        )
        job = bq.load_table_from_json(
            payload,
            f"{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_META_TABLE_ID}",
            job_config=job_config,
        )
        job.result()
        if job.errors:
            raise RuntimeError(f"Metadata load job reported errors: {job.errors[:2]}")
        log(f"Saved {len(rows):,} pages to the metadata warehouse.")
    except Exception as e:
        if BQ_STRICT_WRITES:
            raise
        log(f"Metadata warehouse write failed: {e}")


# ── Feedback ──────────────────────────────────────────────────────────────────

async def sync_feedback_to_bigquery(row: FeedbackRow) -> None:
    if not BQ_PROJECT_ID or not BQ_DATASET_ID:
        return
    try:
        bq = _client()
        ensure_tables(lambda *_a, **_k: None)
        table = _tref(BQ_FEEDBACK_TABLE_ID)
        errors = bq.insert_rows_json(table, [{
            "submitted_at": date.today().isoformat(),
            "query": row.query, "vertical": row.vertical,
            "category": row.category, "topic": row.topic, "domains": row.domains,
        }])
        if errors and BQ_STRICT_WRITES:
            raise RuntimeError(f"BigQuery rejected feedback row: {errors[:1]}")
    except Exception:
        if BQ_STRICT_WRITES:
            raise
        pass  # best-effort — never fail a submission

"""
bigquery.py — BigQuery warehouse read/write.
Port of bigquery.ts. Preserves D11 (bq DATE wrapper), D12 (freshness gate),
D13 (no Sheets fallback).

M7 fix: _client() now strips GCP_SERVICE_ACCOUNT_JSON before the truthiness
check and wraps json.loads in a specific JSONDecodeError handler. The old code
let json.loads("") or json.loads("\n") raise a generic JSONDecodeError which
bubbled up as "BigQuery unavailable (Expecting value: line 1 column 1 (char 0))",
making a misconfigured secret indistinguishable from BigQuery being unreachable.
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

BQ_CHUNK_SIZE = 500  # streaming insert chunk limit

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
              AND date BETWEEN @startDate AND @endDate
            QUALIFY date = MAX(date) OVER (PARTITION BY domain)
        )
        GROUP BY account, domain, query, page
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("domains", "STRING", domains),
            bigquery.ScalarQueryParameter("startDate", "DATE", start_date),
            bigquery.ScalarQueryParameter("endDate", "DATE", end_date),
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
        log(f"✓ Warehouse has {len(out):,} rows (newest {max_date or 'n/a'}, {'fresh' if fresh else 'stale'}).")
    return out, fresh


async def sync_to_bigquery(rows: list[GscRow], log: LogFn = print) -> None:
    """Best-effort write of freshly fetched GSC rows to warehouse. Never throws."""
    if not rows:
        return
    try:
        bq = _client()
        # D11: DATE partition column requires a native datetime.date object.
        # A plain ISO string is silently rejected on streaming inserts when the
        # column is a partition key, making the insert appear to succeed while
        # writing nothing. fromisoformat() produces the required typed value.
        monday = date.fromisoformat(_week_monday())

        # M8 fix: delete this Monday's snapshot before inserting, so re-runs
        # within the same week (manual re-sync, Cloud Scheduler retry, or an
        # accidental double trigger) replace the snapshot instead of appending
        # duplicate rows. CAST(@snapshotDate AS DATE) keeps the parameter a
        # typed DATE at the SQL boundary (same discipline as D11).
        # Best-effort: a delete failure is logged and the insert proceeds —
        # the latest-snapshot read (fetch_from_bigquery) tolerates duplicate
        # rows within one snapshot via MAX().
        try:
            del_sql = f"DELETE FROM {_table_ref()} WHERE date = @snapshotDate"
            del_cfg = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("snapshotDate", "DATE", monday),
                ]
            )
            bq.query(del_sql, job_config=del_cfg).result()
        except Exception as del_exc:
            log(f"! Could not clear existing snapshot for {monday} ({del_exc}) — appending instead.")
        payload = [
            {
                "account": r.account, "domain": r.domain, "query": r.query,
                "page": r.page, "clicks": r.clicks, "impressions": r.impressions,
                "position": r.position, "date": monday,
            }
            for r in rows
        ]
        table = bq.dataset(BQ_DATASET_ID).table(BQ_TABLE_ID)
        total_errors = 0
        for i in range(0, len(payload), BQ_CHUNK_SIZE):
            errors = bq.insert_rows_json(table, payload[i:i + BQ_CHUNK_SIZE])
            if errors:
                total_errors += len(errors)
                log(f"! Warehouse write errors (chunk {i}): {errors[:2]}")
                if BQ_STRICT_WRITES:
                    raise RuntimeError(f"BigQuery rejected {len(errors)} rows at chunk {i}: {errors[:2]}")
        if total_errors:
            log(f"! Warehouse warm-up wrote {len(rows) - total_errors:,} of {len(rows):,} rows ({total_errors} rejected).")
        else:
            log(f"✓ Warmed warehouse with {len(rows):,} rows.")
    except Exception as e:
        if BQ_STRICT_WRITES:
            raise
        log(f"! Warehouse warm-up skipped: {e}")


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
    if not rows:
        return
    try:
        bq = _client()
        snapshot_date = date.today().isoformat()
        payload = [
            {
                "snapshot_date": snapshot_date,
                "page": r.page, "meta_title": r.meta_title,
                "meta_description": r.meta_description,
                "h1": r.h1, "h2": r.h2,
            }
            for r in rows
        ]
        table = bq.dataset(BQ_DATASET_ID).table(BQ_META_TABLE_ID)
        total_errors = 0
        for i in range(0, len(payload), BQ_CHUNK_SIZE):
            errors = bq.insert_rows_json(table, payload[i:i + BQ_CHUNK_SIZE])
            if errors:
                total_errors += len(errors)
                log(f"! Metadata write errors (chunk {i}): {errors[:2]}")
                if BQ_STRICT_WRITES:
                    raise RuntimeError(f"BigQuery rejected {len(errors)} metadata rows at chunk {i}: {errors[:2]}")
        if total_errors:
            log(f"! Metadata warehouse wrote {len(rows) - total_errors:,} of {len(rows):,} pages ({total_errors} rejected).")
        else:
            log(f"✓ Saved {len(rows):,} pages to the metadata warehouse.")
    except Exception as e:
        if BQ_STRICT_WRITES:
            raise
        log(f"! Metadata warehouse write skipped: {e}")


# ── Feedback ──────────────────────────────────────────────────────────────────

async def sync_feedback_to_bigquery(row: FeedbackRow) -> None:
    if not BQ_PROJECT_ID or not BQ_DATASET_ID:
        return
    try:
        bq = _client()
        table = bq.dataset(BQ_DATASET_ID).table(BQ_FEEDBACK_TABLE_ID)
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

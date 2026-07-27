"""
routers/admin.py — Admin job endpoints.
/api/admin/sync-gsc       POST → streams NDJSON progress, fetches all GSC data live, writes to BigQuery
/api/admin/sync-metadata  POST → streams NDJSON progress, crawls all pages, writes to BigQuery
/api/admin/status         GET  → last sync timestamps from BigQuery
/api/admin/auto-sync/{secret_key}
                          GET  → unauthenticated cron endpoint; runs Phase 1 (GSC sync) then
                                 Phase 2 (metadata crawl) back-to-back in a background task;
                                 returns 200 immediately (fire-and-forget).

Engineering notes:
  - /auto-sync is added to GATE_OPEN_PREFIXES in main.py so external cron bots
    can call it without a session cookie. The secret_key path parameter is the
    only access control — keep AUTO_SYNC_SECRET out of logs and source code.
  - The worker uses datetime.date objects for all BigQuery writes (D11) — the
    date field is never cast to str() before being passed to sync_to_bigquery().
  - All exceptions inside the worker are caught and printed; a failure in Phase 1
    or Phase 2 never crashes the server process.
  - The manual streaming endpoints (/sync-gsc and /sync-metadata) are unchanged.
"""
import json
import asyncio
from datetime import date, timedelta
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse
from ..lib.gsc import fetch_all_domains_live, list_gsc_properties
from ..lib.aggregate import aggregate_to_pages
from ..lib.classify import enrich_page_categories, detect_hub_pages, detect_programmatic_series
from ..lib.crawl import crawl_metadata_for_pages
from ..lib.config import (
    FFG_OWNED_DOMAINS, USE_BIGQUERY,
    BQ_PROJECT_ID, BQ_DATASET_ID, BQ_TABLE_ID, BQ_META_TABLE_ID,
    GSC_LAG_DAYS, AUTO_SYNC_SECRET,
)
from ..lib.tokenstore import get_auth_status

router = APIRouter(prefix="/api/admin")

# M8: single-flight guard for the auto-sync worker. Cloud Scheduler retries,
# an overlapping keep-alive misconfiguration, or a manual trigger during a
# scheduled run must not start a second concurrent full sync (double GSC
# quota burn; the delete-before-insert in sync_to_bigquery would also race).
# The flag is flipped inside the single event loop, so no threading lock is
# needed; the supervisor's finally block guarantees release on any exit path.
_auto_sync_running = False


def _date_range() -> tuple[str, str]:
    end = date.today() - timedelta(days=GSC_LAG_DAYS)
    start = end - timedelta(days=365)
    return start.isoformat(), end.isoformat()


async def _require_auth() -> bool:
    """
    Lightweight auth guard for manual admin endpoints.
    Returns True if at least one GSC account has a stored token in Upstash.
    Credential validity is enforced downstream by build_authed_credentials().
    """
    try:
        status = await get_auth_status()
        return status.get("data", False) or status.get("analytics", False)
    except Exception:
        return False


def _stream(coro_factory):
    """Wrap an async job into a NDJSON StreamingResponse."""
    queue: asyncio.Queue = asyncio.Queue()

    async def job_task():
        try:
            await coro_factory(lambda msg: queue.put_nowait({"type": "log", "message": msg}))
        except Exception as exc:
            queue.put_nowait({"type": "error", "message": str(exc)})
        finally:
            queue.put_nowait(None)

    asyncio.create_task(job_task())

    async def event_stream():
        while True:
            event = await queue.get()
            if event is None:
                yield json.dumps({"type": "done"}) + "\n"
                break
            yield json.dumps(event) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/sync-gsc")
async def sync_gsc(request: Request):
    """
    Full live GSC fetch for all configured domains, writes to BigQuery.
    Calls fetch_all_domains_live() — always hits the GSC API, never
    short-circuits on a warm warehouse (D-S3 / D15).
    """
    if not await _require_auth():
        return Response(
            content='{"error":"No Google accounts connected. Go to /setup first."}',
            status_code=401,
            media_type="application/json",
        )

    async def job(log):
        log("▸ Starting GSC sync for all domains (live fetch — bypassing warehouse)...")
        props = await list_gsc_properties()
        domains = props.get("ordered", FFG_OWNED_DOMAINS)
        start_date, end_date = _date_range()
        log(f"▸ Date range: {start_date} → {end_date}")
        log(f"▸ Domains: {len(domains)} properties")

        from ..lib import bigquery as bq_mod

        log("▸ Fetching live GSC data from Search Console...")
        rows = await fetch_all_domains_live(domains, start_date, end_date, log)
        if not rows:
            log("! No GSC data returned. Verify both Google accounts are connected and have GSC access.")
            return
        log(f"✓ GSC sync complete: {len(rows):,} rows fetched.")

        if USE_BIGQUERY:
            await bq_mod.sync_to_bigquery(rows, log)
        else:
            log("! BigQuery not configured — data not persisted. Set BQ_PROJECT_ID and BQ_DATASET_ID.")

    return _stream(job)


@router.post("/sync-metadata")
async def sync_metadata(request: Request):
    """Crawl metadata for all pages in BigQuery GSC data, write to metadata table."""
    if not await _require_auth():
        return Response(
            content='{"error":"No Google accounts connected. Go to /setup first."}',
            status_code=401,
            media_type="application/json",
        )

    async def job(log):
        log("▸ Starting metadata sync...")

        if not USE_BIGQUERY:
            log("! BigQuery not configured — cannot run metadata sync. Set BQ_PROJECT_ID and BQ_DATASET_ID.")
            return

        from ..lib.bigquery import fetch_from_bigquery
        start_date, end_date = _date_range()
        props = await list_gsc_properties()
        domains = props.get("ordered", FFG_OWNED_DOMAINS)

        log(f"▸ Reading pages from warehouse for {len(domains)} domains...")
        rows, _ = await fetch_from_bigquery(domains, start_date, end_date, log)

        if not rows:
            log("! No rows in warehouse — run GSC sync first.")
            return

        log(f"▸ Aggregating {len(rows):,} GSC rows to unique pages...")
        pages = aggregate_to_pages(rows)
        pages = enrich_page_categories(pages, log)
        pages = detect_hub_pages(pages, log)
        pages = detect_programmatic_series(pages, log)
        log(f"▸ {len(pages):,} unique pages to crawl.")

        await crawl_metadata_for_pages(pages, log)
        log("✓ Metadata sync complete.")

    return _stream(job)


@router.get("/auto-sync/{secret_key}")
async def auto_sync(secret_key: str):
    """
    Unauthenticated cron endpoint — runs Phase 1 (GSC sync) then Phase 2
    (metadata crawl) sequentially in a background task and returns 200 immediately.

    Security: validated by secret_key path parameter against AUTO_SYNC_SECRET env
    var. The path prefix /api/admin/auto-sync is added to GATE_OPEN_PREFIXES in
    main.py so external callers need no session cookie.

    Usage (cron job or uptime monitor):
        GET https://{host}/api/admin/auto-sync/{AUTO_SYNC_SECRET}
    """
    # Reject if secret is missing from env (misconfigured deployment) or wrong.
    if not AUTO_SYNC_SECRET:
        return JSONResponse(
            {"status": "Error", "message": "AUTO_SYNC_SECRET is not configured on this deployment"},
            status_code=503,
        )
    if secret_key != AUTO_SYNC_SECRET:
        return JSONResponse(
            {"status": "Unauthorized", "message": "Invalid secret key"},
            status_code=401,
        )

    global _auto_sync_running
    if _auto_sync_running:
        # 200, not 409: Cloud Scheduler treats non-2xx as failure and retries,
        # which is exactly the loop this guard exists to prevent.
        return JSONResponse(
            {"status": "Skipped", "message": "A sync is already running."},
            status_code=200,
        )
    _auto_sync_running = True

    async def combined_background_worker() -> None:
        """
        Phase 1: live GSC fetch → BigQuery write.
        Phase 2: BQ read → page aggregation → metadata crawl → BQ write.

        All exceptions are caught and printed so a Phase 1 or Phase 2 failure
        never crashes the server runtime loop.

        D11 note: date values passed to sync_to_bigquery() are datetime.date
        objects, never str. The str() cast suggested in the cron spec would
        silently reintroduce the partition-key rejection bug fixed in M7.
        """
        from ..lib import bigquery as bq_mod
        from ..lib.bigquery import fetch_from_bigquery

        print("[auto-sync] Starting combined background sync...")

        # ── Phase 1: GSC sync ──────────────────────────────────────────────────
        try:
            # Check at least one account is connected before making live GSC calls.
            if not await _require_auth():
                print("[auto-sync] Phase 1 aborted: no Google accounts connected.")
                return

            props = await list_gsc_properties(print)
            domains = props.get("ordered", FFG_OWNED_DOMAINS)
            start_date, end_date = _date_range()

            print(f"[auto-sync] Phase 1: fetching {len(domains)} domains ({start_date} → {end_date})")
            rows = await fetch_all_domains_live(domains, start_date, end_date, print)

            if not rows:
                print("[auto-sync] Phase 1: no GSC rows returned — skipping Phase 2.")
                return

            print(f"[auto-sync] Phase 1: {len(rows):,} rows fetched.")

            if USE_BIGQUERY:
                # D11: datetime.date objects are passed through — no str() cast.
                await bq_mod.sync_to_bigquery(rows, print)
                print("[auto-sync] Phase 1: warehouse write complete.")
            else:
                print("[auto-sync] Phase 1: BigQuery not configured, data not persisted.")

        except Exception as exc:
            print(f"[auto-sync] Phase 1 error: {exc}")
            return  # Phase 2 depends on Phase 1 data — stop here on failure.

        # ── Phase 2: metadata crawl ────────────────────────────────────────────
        try:
            if not USE_BIGQUERY:
                print("[auto-sync] Phase 2 skipped: BigQuery not configured.")
                return

            print("[auto-sync] Phase 2: reading updated warehouse...")
            rows_bq, _ = await fetch_from_bigquery(domains, start_date, end_date, print)

            if not rows_bq:
                print("[auto-sync] Phase 2: warehouse appears empty after Phase 1 write — skipping crawl.")
                return

            print(f"[auto-sync] Phase 2: {len(rows_bq):,} rows from warehouse, aggregating pages...")
            pages = aggregate_to_pages(rows_bq)
            pages = enrich_page_categories(pages, print)
            pages = detect_hub_pages(pages, print)
            pages = detect_programmatic_series(pages, print)

            print(f"[auto-sync] Phase 2: crawling metadata for {len(pages):,} pages...")
            await crawl_metadata_for_pages(pages, print)
            print("[auto-sync] Phase 2: metadata crawl complete.")

        except Exception as exc:
            print(f"[auto-sync] Phase 2 error: {exc}")

        print("[auto-sync] Combined background sync finished.")

    async def supervised_worker() -> None:
        """M8: guarantee the single-flight flag is released on every exit path
        (normal completion, early return, or an escaped exception)."""
        global _auto_sync_running
        try:
            await combined_background_worker()
        finally:
            _auto_sync_running = False

    # Fire-and-forget: launch the worker and return 200 immediately.
    asyncio.create_task(supervised_worker())

    return JSONResponse({
        "status": "Success",
        "message": "Full pipeline execution initiated in background",
    })


@router.get("/status")
async def status():
    """Return last sync dates from BigQuery."""
    if not USE_BIGQUERY:
        return {"bigquery": False, "gsc_last_sync": None, "meta_last_sync": None}
    try:
        from ..lib.bigquery import _client
        bq = _client()

        gsc_sql = f"SELECT MAX(date) as last_date FROM `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_TABLE_ID}`"
        meta_sql = f"SELECT MAX(snapshot_date) as last_date FROM `{BQ_PROJECT_ID}.{BQ_DATASET_ID}.{BQ_META_TABLE_ID}`"

        gsc_rows = list(bq.query(gsc_sql).result())
        meta_rows = list(bq.query(meta_sql).result())

        gsc_last = str(gsc_rows[0].last_date) if gsc_rows and gsc_rows[0].last_date else None
        meta_last = str(meta_rows[0].last_date) if meta_rows and meta_rows[0].last_date else None

        return {"bigquery": True, "gsc_last_sync": gsc_last, "meta_last_sync": meta_last}
    except Exception as e:
        return {"bigquery": True, "error": str(e), "gsc_last_sync": None, "meta_last_sync": None}

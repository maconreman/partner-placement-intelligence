"""
jobs/sync.py — Standalone warehouse-sync entrypoint (Alpha M9).

Runs the full ingestion pipeline OUTSIDE the web app, in a plain Python process.
This is the same Phase 1 (live GSC fetch) + Phase 2 (metadata crawl) logic that
`routers/admin.py::auto_sync` runs in a fire-and-forget background task on the
Space, lifted into a script so it can run on a GitHub Actions runner instead.

Why this exists (M9 core change):
  - The Space's ingestion path had three structural problems that no amount of
    concurrency tuning fixes: it slept after ~48h idle, it inherited the request
    lifetime it was launched from, and it was triggered by an unauthenticated
    secret-in-URL cron endpoint on a public app. Moving ingestion to a scheduled
    runner removes all three at once.
  - Tokens live in Upstash and BigQuery credentials come from an env var, so a
    runner with the same secrets can do everything the app can. No app is
    involved in a sync anymore.

What this deliberately does NOT change:
  - The GSC fetch, the 70/30 scoring, the composite-before-dedup rule, the locked
    output schema, and the weekly-snapshot write model are all untouched. This is
    a relocation of *where* ingestion runs, not a change to *what* it computes.
  - Incremental delta fetch (7 days instead of 365) is M9.1 and needs its own
    schema spec — it changes GscRow's shape. It is not in this job.

Run locally:
    python -m jobs.sync

Run one phase only:
    python -m jobs.sync --phase gsc
    python -m jobs.sync --phase metadata

Exit code is non-zero on failure so a CI runner marks the job red.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

# ── Env bootstrap (mirrors main.py D14: env must be present before any backend
# import, because backend.lib.config reads env vars and loads data files at
# import time). On a runner the env comes from Actions secrets; locally we try a
# .env for developer convenience. python-dotenv is optional — absence is fine.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass


def _log(msg: str) -> None:
    """Stdout is captured verbatim by the Actions runner log."""
    print(msg, flush=True)


def _date_range() -> tuple[str, str]:
    """Identical window to routers/admin.py: end = today - lag, start = -365d."""
    from backend.lib.config import GSC_LAG_DAYS
    end = date.today() - timedelta(days=GSC_LAG_DAYS)
    start = end - timedelta(days=365)
    return start.isoformat(), end.isoformat()


async def _run_gsc_sync() -> int:
    """Phase 1: live GSC fetch for every configured domain, persisted to BigQuery.

    fetch_all_domains_live() writes freshly-fetched rows to BigQuery internally
    (see gsc.py::_live_gsc_fetch), so there is no separate sync_to_bigquery call
    here. Under M8's WRITE_TRUNCATE load job the write is idempotent anyway.
    Returns the number of rows fetched.
    """
    from backend.lib.config import FFG_OWNED_DOMAINS, USE_BIGQUERY
    from backend.lib.gsc import fetch_all_domains_live, list_gsc_properties

    if not USE_BIGQUERY:
        _log(
            "BigQuery is not configured, so a sync would persist nothing. "
            "Set BQ_PROJECT_ID, BQ_DATASET_ID and GCP_SERVICE_ACCOUNT_JSON."
        )
        raise SystemExit(2)

    props = await list_gsc_properties(_log)
    domains = props.get("ordered") or list(FFG_OWNED_DOMAINS)
    start_date, end_date = _date_range()
    _log(f"Date range: {start_date} to {end_date}")
    _log(f"Domains: {len(domains)} properties")

    _log("Fetching live GSC data from Search Console.")
    rows = await fetch_all_domains_live(domains, start_date, end_date, _log)
    if not rows:
        _log(
            "No GSC data returned. Confirm both Google accounts are connected at "
            "/setup and that the service account has BigQuery Data Editor."
        )
        raise SystemExit(3)

    _log(f"GSC sync complete: {len(rows):,} rows fetched and persisted.")
    return len(rows)


async def _run_metadata_sync() -> int:
    """Phase 2: read the warehouse back, aggregate to pages, crawl metadata.

    Depends on Phase 1 data existing in the warehouse. Returns the page count.
    """
    from backend.lib.config import FFG_OWNED_DOMAINS, USE_BIGQUERY
    from backend.lib.gsc import list_gsc_properties
    from backend.lib.bigquery import fetch_from_bigquery
    from backend.lib.aggregate import aggregate_to_pages
    from backend.lib.classify import (
        enrich_page_categories, detect_hub_pages, detect_programmatic_series,
    )
    from backend.lib.crawl import crawl_metadata_for_pages

    if not USE_BIGQUERY:
        _log("BigQuery is not configured, so the metadata crawl cannot run.")
        raise SystemExit(2)

    start_date, end_date = _date_range()
    props = await list_gsc_properties(_log)
    domains = props.get("ordered") or list(FFG_OWNED_DOMAINS)

    _log(f"Reading pages from the warehouse for {len(domains)} domains.")
    rows, _fresh = await fetch_from_bigquery(domains, start_date, end_date, _log)
    if not rows:
        _log("The warehouse is empty. Run the GSC phase first.")
        raise SystemExit(3)

    _log(f"Aggregating {len(rows):,} GSC rows to unique pages.")
    pages = aggregate_to_pages(rows)
    pages = enrich_page_categories(pages, _log)
    pages = detect_hub_pages(pages, _log)
    pages = detect_programmatic_series(pages, _log)
    _log(f"{len(pages):,} unique pages to crawl.")

    await crawl_metadata_for_pages(pages, _log)
    _log("Metadata sync complete.")
    return len(pages)


async def _run(phase: str) -> None:
    if phase in ("gsc", "all"):
        _log("=== Phase 1: GSC sync ===")
        await _run_gsc_sync()
    if phase in ("metadata", "all"):
        _log("=== Phase 2: metadata crawl ===")
        await _run_metadata_sync()
    _log("Warehouse sync finished.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Nexus warehouse sync (M9).")
    parser.add_argument(
        "--phase",
        choices=("all", "gsc", "metadata"),
        default="all",
        help="Which phase to run. Default runs GSC then metadata.",
    )
    args = parser.parse_args()

    # Recommend strict writes on a runner: a silent BigQuery write failure should
    # turn the job red, not pass quietly. The workflow sets BQ_STRICT_WRITES=true.
    if os.environ.get("BQ_STRICT_WRITES", "").lower() not in ("1", "true", "yes"):
        _log(
            "Note: BQ_STRICT_WRITES is not set. A warehouse write failure will be "
            "logged but will not fail this job. Set it to true on CI runners."
        )

    try:
        asyncio.run(_run(args.phase))
    except SystemExit:
        raise
    except Exception as exc:  # any unhandled error → red job
        _log(f"FATAL: sync failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

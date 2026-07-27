"""
pipeline.py — Pipeline orchestrator. Port of pipeline.ts.
Stage order: fetch → pages → seo → metadata → content_type → match → refine → rank.
Emits structured events for NDJSON streaming.
"""
from __future__ import annotations
import asyncio
from typing import AsyncGenerator, Any

from .gsc import fetch_all_domains
from .aggregate import aggregate_to_pages, compute_seo_score
from .classify import enrich_page_categories, detect_hub_pages, detect_programmatic_series, enrich_content_types
from .crawl import crawl_metadata_for_pages, prewarm_pages_into_cache
from .quickmatch import quick_match_candidates
from .relevance import compute_relevance_scores, warmup_embed_model, HFAPIError
from .rank import build_results, to_export_rows

STAGE_IDS = ["fetch", "pages", "seo", "metadata", "match", "refine", "rank"]


async def run_pipeline(
    domains: list[str],
    topic: str,
    start_date: str,
    end_date: str,
    emit,  # callable(dict) -> None
) -> None:
    def log(message: str):
        emit({"type": "log", "message": message})

    def stage(s: str, status: str):
        emit({"type": "stage", "stage": s, "status": status})

    # Stage 1 — Fetch + HF warm-up
    # M4 item 2: overlap the metadata crawl with the GSC fetch. As each domain's
    # rows arrive (on cold/live runs only), enqueue its page URLs; a background
    # consumer crawls them into the in-memory metadata cache (item 3) while later
    # domains are still being fetched and while the pages/seo stages run. The
    # authoritative crawl in Stage 4 is unchanged — it simply finds these pages
    # already warm, so result quality and scoring are identical. On a warm
    # BigQuery warehouse, fetch_all_domains returns early, no callback fires, and
    # the overlap is moot (the queue only ever receives the sentinel).
    stage("fetch", "active")
    asyncio.create_task(warmup_embed_model())  # fire and forget

    crawl_queue: asyncio.Queue = asyncio.Queue()
    prewarmed = [0]

    def on_domain_complete(domain: str, domain_rows: list) -> None:
        # Called from concurrent fetch tasks (same event loop) — put_nowait is safe.
        crawl_queue.put_nowait([r.page for r in domain_rows if getattr(r, "page", "")])

    async def prewarm_consumer() -> None:
        while True:
            batch = await crawl_queue.get()
            try:
                if batch is None:
                    return
                prewarmed[0] += await prewarm_pages_into_cache(batch)
            except Exception:
                pass  # overlap is best-effort; never break the run
            finally:
                crawl_queue.task_done()

    consumer_task = asyncio.create_task(prewarm_consumer())

    rows = await fetch_all_domains(
        domains, start_date, end_date, log, on_domain_complete=on_domain_complete
    )
    crawl_queue.put_nowait(None)  # signal end of fetch — consumer drains and exits
    stage("fetch", "done")

    if not rows:
        emit({"type": "error", "code": "no_data", "message": "No GSC data returned. Try a wider date range."})
        return
    rows_fetched = len(rows)

    # Stage 2 — Unique pages (classify + aggregate → L3 → hub → programmatic)
    stage("pages", "active")
    pages = aggregate_to_pages(rows)
    pages = enrich_page_categories(pages, log)
    pages = detect_hub_pages(pages, log)
    pages = detect_programmatic_series(pages, log)
    stage("pages", "done")
    n_pages = len(pages)

    # Stage 3 — SEO score
    stage("seo", "active")
    seo_scores = compute_seo_score(pages)
    for i, p in enumerate(pages):
        p.seo_score = seo_scores[i]
    stage("seo", "done")

    # Stage 4 — Metadata crawl + content-type classification
    stage("metadata", "active")
    # M4 item 2: ensure the overlapped pre-warm has finished populating the
    # in-memory cache before the authoritative crawl runs, so it serves warm.
    try:
        await consumer_task
        if prewarmed[0]:
            log(f"✓ Pre-warmed {prewarmed[0]:,} pages during fetch (overlapped crawl).")
    except Exception:
        pass
    meta_rows = await crawl_metadata_for_pages(pages, log)
    meta_by_page = {m.page: m for m in meta_rows}
    for p in pages:
        m = meta_by_page.get(p.page)
        p.meta_title = m.meta_title if m else ""
        p.meta_description = m.meta_description if m else ""
        p.h1 = m.h1 if m else ""
        p.h2 = m.h2 if m else ""
    # M3.5: content-type classification runs after metadata is merged
    pages = enrich_content_types(pages, log)
    stage("metadata", "done")

    # Stage 5 — Quick match
    stage("match", "active")
    cand = quick_match_candidates(pages, topic, log=log)
    stage("match", "done")
    n_matched = len(cand)

    if not cand:
        emit({"type": "funnel", "rowsFetched": rows_fetched, "pages": n_pages, "matched": 0, "scored": 0})
        emit({"type": "error", "code": "no_relevance", "message": "No relevant pages found. Try a broader topic."})
        return

    # Stage 6 — HF Relevance (D1: no TF-IDF fallback)
    stage("refine", "active")
    try:
        scores = await compute_relevance_scores(cand, topic, log)
    except HFAPIError as exc:
        stage("refine", "idle")
        emit({"type": "error", "code": "hf_error", "message": str(exc)})
        return
    for i, c in enumerate(cand):
        c.topical_relevance_score = scores[i]
    stage("refine", "done")

    # Stage 7 — Composite rank (D2: composite BEFORE dedup)
    stage("rank", "active")
    results = build_results(cand)
    stage("rank", "done")

    if not results:
        emit({"type": "funnel", "rowsFetched": rows_fetched, "pages": n_pages, "matched": n_matched, "scored": 0})
        emit({"type": "error", "code": "no_relevance", "message": "No relevant pages found. Try a broader topic."})
        return

    emit({"type": "funnel", "rowsFetched": rows_fetched, "pages": n_pages, "matched": n_matched, "scored": len(results)})
    emit({
        "type": "result",
        "rows": to_export_rows(results),
        "preview": [
            {
                "rank": r.rank,
                "page": r.page,
                "matched_on": r.matched_on,
                "anchor_text": r.anchor_text,
                "anchor_source": r.anchor_source,
                "topical_relevance_score": r.topical_relevance_score,
                "seo_score": r.seo_score,
                "page_category": r.page_category,
                "content_type": r.content_type,
                "tier_label": r.tier_label,
                "query": r.query,
                "meta_title": r.meta_title,
                "meta_description": r.meta_description,
                "h1": r.h1,
                "clicks": r.clicks,
                "impressions": r.impressions,
                "position": r.position,
            }
            for r in results[:10]
        ],
    })

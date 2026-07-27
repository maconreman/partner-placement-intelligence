"""
crawl.py — Metadata crawl with 16KB partial fetch + </head> abort.
Port of crawl.ts with the Colab partial-fetch optimization now applied (was
previously Colab-only; D-S4 says it should be in the FastAPI backend).
"""
from __future__ import annotations
import re
import time
import asyncio
import httpx
from bs4 import BeautifulSoup
from typing import Optional

from .config import USE_BIGQUERY, META_CACHE_STALENESS
from .util import PageRow, MetaRow, LogFn, url_host, map_with_concurrency
from .bigquery import read_meta_from_bigquery, sync_meta_to_bigquery

METADATA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Range": "bytes=0-16383",  # D-S4: partial fetch — only grab the <head>
}

CRAWL_WORKERS = 96
CRAWL_TIMEOUT = 3.0

# ── M4 item 3: process-lifetime in-memory metadata cache ──────────────────────
# Read-through layer in front of BigQuery. Within one process, the second and
# third analysis of the same URL skip both the BigQuery read and the live crawl.
# BigQuery remains the durable store; this cache does not survive a container
# restart. TTL matches META_CACHE_STALENESS (90 days) from config.
_meta_cache: dict[str, tuple[MetaRow, float]] = {}  # url → (row, timestamp)
META_CACHE_TTL = META_CACHE_STALENESS * 24 * 3600


def _cache_get(url: str) -> Optional[MetaRow]:
    entry = _meta_cache.get(url)
    if entry and (time.time() - entry[1]) < META_CACHE_TTL:
        return entry[0]
    return None


def _cache_set(url: str, row: MetaRow) -> None:
    _meta_cache[url] = (row, time.time())

_SECTION_HUB_NOUNS = re.compile(
    r"\b(blog|blogs|guide|guides|resource|resources|article|articles|post|posts|"
    r"news|insight|insights|learn|library|tip|tips|update|updates)\b",
    re.IGNORECASE,
)
_SKIP_URL_PATTERNS = re.compile(
    r"/(contact|privacy|terms|login|signin|cart|checkout|category|tag|author|page)/?|/blog/?$",
    re.IGNORECASE,
)


def is_section_hub_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        path = urlparse(url.split("#")[0]).path.rstrip("/")
        terminal = path.split("/")[-1] if path else ""
        return bool(_SECTION_HUB_NOUNS.search(terminal))
    except Exception:
        return False


def _empty_meta(page: str) -> MetaRow:
    return MetaRow(page=page, meta_title="", meta_description="", h1="", h2="")


async def _fetch_single_page_metadata(url: str, client: Optional[httpx.AsyncClient] = None) -> MetaRow:
    """
    Crawl one page's <head> metadata.

    M4 item 4: when `client` is provided, reuse the shared AsyncClient (and its
    pooled keep-alive connections). When omitted, fall back to a per-call client
    so existing callers keep working unchanged.
    """
    clean = url.split("#")[0]

    async def _do(c: httpx.AsyncClient) -> MetaRow:
        resp = await c.get(clean, headers=METADATA_HEADERS)
        if resp.status_code not in (200, 206):
            return _empty_meta(url)
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower():
            return _empty_meta(url)

        # D-S4: abort stream after </head> to avoid downloading full body
        raw_bytes = resp.content
        text = raw_bytes.decode("utf-8", errors="replace")
        head_end = text.lower().find("</head>")
        if head_end != -1:
            text = text[:head_end + 7] + "</html>"

        # Use xml parser for declared XML, html.parser otherwise
        parser = "xml" if "application/xml" in ctype or "text/xml" in ctype else "html.parser"
        soup = BeautifulSoup(text, parser)

        title = soup.find("title")
        meta_title = title.get_text(strip=True) if title else ""

        desc_tag = soup.find("meta", attrs={"name": re.compile(r"^description$", re.IGNORECASE)})
        meta_desc = (desc_tag.get("content", "") if desc_tag else "").strip()  # type: ignore

        h1_tags = soup.find_all("h1")
        h1 = " | ".join(t.get_text(strip=True) for t in h1_tags if t.get_text(strip=True))

        h2_tags = soup.find_all("h2")
        h2 = " | ".join(t.get_text(strip=True) for t in h2_tags if t.get_text(strip=True))

        return MetaRow(page=url, meta_title=meta_title, meta_description=meta_desc, h1=h1, h2=h2)

    try:
        if client is not None:
            return await _do(client)
        async with httpx.AsyncClient(timeout=CRAWL_TIMEOUT, follow_redirects=True) as own:
            return await _do(own)
    except Exception:
        return _empty_meta(url)


async def crawl_metadata_for_pages(pages: list[PageRow], log: LogFn = print) -> list[MetaRow]:
    if not pages:
        return []

    # ── Level 0 filter ────────────────────────────────────────────────────────
    valid_set: set[str] = set()
    for row in pages:
        if _SKIP_URL_PATTERNS.search(row.page):
            continue
        if is_section_hub_url(row.page):
            continue
        if row.page_category in ("Contact", "Homepage", "Hub"):
            continue
        valid_set.add(row.page)

    valid_pages = list(valid_set)
    total_unique = len({p.page for p in pages})
    log(f"▸ Level 0 Filter: Dropped {total_unique - len(valid_pages)} structural dead-ends/hubs. Evaluating {len(valid_pages)} viable URLs.")

    if not valid_pages:
        return []

    all_frames: list[MetaRow] = []
    to_crawl: list[str] = []

    # ── M4 item 3: in-memory cache layer (checked BEFORE BigQuery) ────────────
    # Pages already crawled this process lifetime are served from memory and are
    # never sent to BigQuery.
    remaining: list[str] = []
    mem_hits = 0
    for u in valid_pages:
        hit = _cache_get(u)
        if hit is not None:
            all_frames.append(hit)
            mem_hits += 1
        else:
            remaining.append(u)
    if mem_hits:
        log(f"✓ Loaded {mem_hits:,} pages from the in-memory cache.")

    if not remaining:
        return all_frames

    # BigQuery warehouse read (only for pages not already in memory)
    cached_by_page: dict[str, MetaRow] = {}
    if USE_BIGQUERY:
        try:
            cached = await read_meta_from_bigquery(remaining)
            cached_by_page = {c.page: c for c in cached}
            if cached:
                log(f"✓ Loaded {len(cached):,} pages from the metadata warehouse.")
        except Exception as e:
            log(f"! Metadata warehouse read skipped: {e}")

    for u in remaining:
        if u in cached_by_page:
            row = cached_by_page[u]
            all_frames.append(row)
            _cache_set(u, row)  # populate in-memory cache from warehouse read
        else:
            to_crawl.append(u)

    if not to_crawl:
        return all_frames

    log(f"▸ Live Crawl: Fetching {len(to_crawl):,} un-cached pages (Timeout={CRAWL_TIMEOUT}s, Threads={CRAWL_WORKERS})...")

    done = [0]
    total = len(to_crawl)

    # ── M4 item 4: one shared AsyncClient with a pooled connection limit ──────
    limits = httpx.Limits(max_connections=128, max_keepalive_connections=64)
    async with httpx.AsyncClient(timeout=CRAWL_TIMEOUT, follow_redirects=True, limits=limits) as shared_client:
        async def crawl_one(url: str) -> MetaRow:
            meta = await _fetch_single_page_metadata(url, client=shared_client)
            _cache_set(url, meta)  # M4 item 3: populate cache on fresh crawl
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == total:
                log(f"  ... Crawled {done[0]}/{total}")
            return meta

        fresh = await map_with_concurrency(to_crawl, CRAWL_WORKERS, crawl_one)

    if USE_BIGQUERY and fresh:
        await sync_meta_to_bigquery(fresh, log)

    all_frames.extend(fresh)
    return all_frames


# ── M4 item 2: crawl pre-warm helper used by the pipeline overlap ─────────────
def prewarm_url_filter(url: str) -> bool:
    """
    URL-only subset of the Level 0 filter (D-S1) — the category-based exclusions
    (Contact/Homepage/Hub) are not yet known during the GSC fetch, so the
    pre-warm pass applies only the pure-URL gates. It therefore never *excludes*
    a page the authoritative pass would keep; at worst it warms a few extra
    pages, which are simply never looked up. Result quality is unchanged because
    the authoritative crawl_metadata_for_pages() still runs the full filter.
    """
    if _SKIP_URL_PATTERNS.search(url):
        return False
    if is_section_hub_url(url):
        return False
    return True


async def prewarm_pages_into_cache(urls: list[str]) -> int:
    """
    Crawl the given URLs into the in-memory metadata cache using a shared client.
    Skips URLs already cached. Returns the number of pages newly crawled.
    Best-effort: individual failures fall back to empty MetaRow (still cached so
    we don't re-crawl a dead URL during the authoritative pass).
    """
    todo = [u for u in {u for u in urls if u} if prewarm_url_filter(u) and _cache_get(u) is None]
    if not todo:
        return 0
    limits = httpx.Limits(max_connections=128, max_keepalive_connections=64)
    async with httpx.AsyncClient(timeout=CRAWL_TIMEOUT, follow_redirects=True, limits=limits) as shared_client:
        async def warm_one(url: str) -> None:
            meta = await _fetch_single_page_metadata(url, client=shared_client)
            _cache_set(url, meta)
        await map_with_concurrency(todo, CRAWL_WORKERS, warm_one)
    return len(todo)

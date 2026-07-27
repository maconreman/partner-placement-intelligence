"""
util.py — Shared row shapes and numeric helpers.
Port of util.ts / types from the Next.js implementation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional
import math
import asyncio


# ── Row shapes ────────────────────────────────────────────────────────────────

@dataclass
class GscRow:
    account: str
    domain: str
    query: str
    page: str
    clicks: int
    impressions: int
    position: float


@dataclass
class PageRow:
    account: str
    domain: str
    page: str
    page_category: str
    clicks: int
    impressions: int
    position: float
    query: str         # top query (highest clicks)
    query_all: str     # pipe-joined query bag (cap 50)
    seo_score: Optional[float] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    h1: Optional[str] = None
    h2: Optional[str] = None
    content_type: Optional[str] = None  # M3.5: Listicle | How-to | Comparison | None


@dataclass
class CandidateRow:
    # All PageRow fields
    account: str
    domain: str
    page: str
    page_category: str
    clicks: int
    impressions: int
    position: float
    query: str
    query_all: str
    seo_score: Optional[float] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    h1: Optional[str] = None
    h2: Optional[str] = None
    content_type: Optional[str] = None
    # CandidateRow additions
    matched_on: str = ""
    anchor_text: str = ""
    anchor_source: str = ""
    lexical_score: int = 0
    surface_max: Optional[int] = None
    topical_relevance_score: Optional[float] = None


@dataclass
class ResultRow:
    account: str
    domain: str
    page: str
    page_category: str
    clicks: int
    impressions: int
    position: float
    query: str
    query_all: str
    seo_score: Optional[float] = None
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    h1: Optional[str] = None
    h2: Optional[str] = None
    content_type: Optional[str] = None
    matched_on: str = ""
    anchor_text: str = ""
    anchor_source: str = ""
    lexical_score: int = 0
    topical_relevance_score: Optional[float] = None
    composite_score: float = 0.0
    tier_label: str = ""
    is_blog_flag: int = 0
    rank: int = 0


@dataclass
class MetaRow:
    page: str
    meta_title: str
    meta_description: str
    h1: str
    h2: str


@dataclass
class FeedbackRow:
    query: str
    vertical: str
    category: str
    topic: str
    domains: str


LogFn = Callable[[str], None]


# ── URL helpers ───────────────────────────────────────────────────────────────

def url_path(url: str) -> str:
    """Return lowercased pathname, hash stripped."""
    try:
        from urllib.parse import urlparse
        clean = url.split("#")[0]
        return urlparse(clean).path.lower()
    except Exception:
        return ""


def url_host(url: str) -> str:
    """Return host without www prefix."""
    try:
        from urllib.parse import urlparse
        clean = url.split("#")[0]
        host = urlparse(clean).netloc
        return host.removeprefix("www.")
    except Exception:
        return ""


# ── scipy.stats.rankdata(method="average") → ranks/n in [0,1] ────────────────

def percentile_norm(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    if len(set(values)) <= 1:
        return [0.0] * n

    # Average ranks for ties (1-indexed, matching scipy)
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return [r / n for r in ranks]


def clamp_round1(x: float, lo: float, hi: float) -> float:
    c = min(max(x, lo), hi)
    return round(c * 10) / 10


# ── Vector math (replaces numpy for our use case) ────────────────────────────

def l2normalize(v: list[float]) -> list[float]:
    s = sum(x * x for x in v)
    n = math.sqrt(s) + 1e-9
    return [x / n for x in v]


def cosine_to_query(q_unit: list[float], mat: list[list[float]]) -> list[float]:
    results = []
    for row in mat:
        r = l2normalize(row)
        dot = sum(q_unit[i] * r[i] for i in range(len(q_unit)))
        results.append(dot)
    return results


# ── Bounded async concurrency (ThreadPoolExecutor analog) ────────────────────

async def map_with_concurrency(items: list, limit: int, worker):
    """Run async worker over items with at most `limit` concurrent tasks."""
    results = [None] * len(items)
    semaphore = asyncio.Semaphore(limit)

    async def run(i, item):
        async with semaphore:
            results[i] = await worker(item)

    await asyncio.gather(*[run(i, item) for i, item in enumerate(items)])
    return results

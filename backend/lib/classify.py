"""
classify.py — Page classifier + M3.5 content-type classifier.
Port of classify.ts. Hard rules preserved:
  D3: Layer 3 must NOT inherit from domain root (segs < 2 → no parent).
  D8: Programmatic series children are KEPT (re-tagged 'Other'), never dropped.
"""
from __future__ import annotations
import re
from typing import Optional

from .config import (
    CATEGORY_PATTERNS, CATEGORY_VOCAB, LAYER2_MIN_VOCAB_HITS, PROGRAMMATIC_SERIES_MIN,
)
from .util import PageRow, LogFn, url_path

REAL_CATEGORIES = {"Blog", "Landing Page", "Product / Service", "Contact"}


# ── Layer 1 + Layer 2 ─────────────────────────────────────────────────────────

def classify_page(url: str) -> str:
    try:
        path = url_path(url)
    except Exception:
        return "Other"
    if path == "" or path == "/":
        return "Homepage"

    # Layer 1 — structural pattern match
    for category, patterns in CATEGORY_PATTERNS.items():
        if any(p in path for p in patterns):
            return category

    # Layer 2 — slug vocabulary scoring
    tokens = set(re.split(r"[-_/]", path))
    best_cat = "Other"
    best_hits = 0
    for category, vocab in CATEGORY_VOCAB.items():
        hits = sum(1 for t in tokens if t in vocab)
        if hits > best_hits:
            best_cat = category
            best_hits = hits
    return best_cat if best_hits >= LAYER2_MIN_VOCAB_HITS else "Other"


def _parent_key(url: str) -> Optional[str]:
    """(domain, parentPath) key, or None for top-level pages (D3)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url.split("#")[0])
        dom = parsed.netloc.removeprefix("www.")
        segs = [s for s in parsed.path.split("/") if s]
        if len(segs) < 2:
            return None  # D3: top-level pages never inherit
        return f"{dom}|{'/'.join(segs[:-1])}"
    except Exception:
        return None


def _self_key(url: str) -> Optional[str]:
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url.split("#")[0])
        dom = parsed.netloc.removeprefix("www.")
        segs = [s for s in parsed.path.split("/") if s]
        return f"{dom}|{'/'.join(segs)}" if segs else None
    except Exception:
        return None


# ── Layer 3 — corpus-aware parent-path inheritance ────────────────────────────

def enrich_page_categories(pages: list[PageRow], log: LogFn = print) -> list[PageRow]:
    if not pages:
        return pages

    by_parent: dict[str, dict[str, int]] = {}
    for p in pages:
        k = _parent_key(p.page)
        if k is None or p.page_category not in REAL_CATEGORIES:
            continue
        if k not in by_parent:
            by_parent[k] = {}
        by_parent[k][p.page_category] = by_parent[k].get(p.page_category, 0) + 1

    parent_cat: dict[str, str] = {}
    for k, m in by_parent.items():
        best = max(m, key=lambda c: m[c])
        parent_cat[k] = best

    before = sum(1 for p in pages if p.page_category == "Other")
    for p in pages:
        if p.page_category != "Other":
            continue
        k = _parent_key(p.page)
        if k is not None and k in parent_cat:
            p.page_category = parent_cat[k]
    after = sum(1 for p in pages if p.page_category == "Other")
    if before > after:
        log(f"▸ Category enrichment: {before - after} 'Other' pages inherited a parent category.")
    return pages


# ── Hub detection ─────────────────────────────────────────────────────────────

def detect_hub_pages(pages: list[PageRow], log: LogFn = print) -> list[PageRow]:
    if not pages:
        return pages
    parent_set: set[str] = set()
    for p in pages:
        k = _parent_key(p.page)
        if k is not None:
            parent_set.add(k)
    n = 0
    for p in pages:
        sk = _self_key(p.page)
        is_hub = sk is not None and sk in parent_set
        if is_hub and p.page_category not in ("Contact", "Other"):
            p.page_category = "Hub"
            n += 1
    if n > 0:
        log(f"▸ Hub pages: {n} section index pages flagged.")
    return pages


# ── Programmatic series (D8 — KEPT not dropped) ───────────────────────────────

def detect_programmatic_series(pages: list[PageRow], log: LogFn = print) -> list[PageRow]:
    if not pages:
        return pages
    counts: dict[str, int] = {}
    for p in pages:
        if p.page_category != "Other":
            continue
        k = _parent_key(p.page)
        if k is not None:
            counts[k] = counts.get(k, 0) + 1

    flagged = {k: c for k, c in counts.items() if c >= PROGRAMMATIC_SERIES_MIN}
    if not flagged:
        return pages

    n = 0
    for p in pages:
        if p.page_category != "Other":
            continue
        k = _parent_key(p.page)
        if k is not None and k in flagged:
            p.page_category = "Other"  # surfaced for review
            n += 1
    log(f"▸ Programmatic series: {len(flagged)} pattern(s), {n} subfolder children re-tagged as 'Other'.")
    top = sorted(flagged.items(), key=lambda x: -x[1])[:8]
    for k, c in top:
        dom, parent = k.split("|", 1)
        log(f"   • /{parent}/ on {dom} ({c} pages)")
    return pages


# ── M3.5: Content-type classifier ────────────────────────────────────────────
# Runs AFTER metadata crawl (needs H1/H2). Detects Listicle, How-to, Comparison.
# Priority order: Comparison > Listicle > How-to (Comparison is most specific).

_LISTICLE_TITLE_WORDS = {"best", "top", "ultimate", "list", "picks"}
_LISTICLE_SLUG_PATTERNS = re.compile(r"(?:^|[-/])(?:best|top|ultimate)[-/]|[-/]list(?:[-/]|$)")
_HOWTO_SLUG_PATTERNS = re.compile(r"(?:^|[-/])how[-_]to[-/]|[-/](?:guide|tutorial|steps?)(?:[-/]|$)")
_HOWTO_TITLE_WORDS = {"how", "guide", "tutorial", "step", "steps", "learn"}
_COMPARISON_SLUG_PATTERNS = re.compile(r"[-/]vs[-/]|versus|[-/]alternatives?(?:[-/]|$)|[-/]review(?:s?)(?:[-/]|$)")
_COMPARISON_TITLE_WORDS = {"vs", "versus", "alternatives", "alternative", "compared", "comparison", "review", "reviews"}
_NUMBERED_H2 = re.compile(r"(?:^|\| )\d+[\.\)]\s")  # "1. Thing | 2. Other"


def classify_content_type(page: PageRow) -> Optional[str]:
    """
    Classify a page as Listicle, How-to, or Comparison.
    Returns None if no type matches.
    Priority: Comparison > Listicle > How-to.
    """
    try:
        from urllib.parse import urlparse
        path = urlparse(page.page.split("#")[0]).path.lower()
    except Exception:
        path = ""

    title = (page.meta_title or "").lower()
    h1 = (page.h1 or "").lower()
    h2 = (page.h2 or "").lower()
    query = (page.query or "").lower()

    # ── Comparison (most specific — check first) ──────────────────────────────
    if (
        _COMPARISON_SLUG_PATTERNS.search(path)
        or any(w in title.split() for w in _COMPARISON_TITLE_WORDS)
        or any(w in h1.split() for w in _COMPARISON_TITLE_WORDS)
        or "vs" in query or "versus" in query or "alternative" in query
    ):
        return "Comparison"

    # ── Listicle ──────────────────────────────────────────────────────────────
    title_words = set(title.split())
    h1_words = set(h1.split())
    has_listicle_title = bool(title_words & _LISTICLE_TITLE_WORDS or h1_words & _LISTICLE_TITLE_WORDS)
    has_numbered_h2s = bool(_NUMBERED_H2.search(h2))
    has_listicle_slug = bool(_LISTICLE_SLUG_PATTERNS.search(path))
    if has_listicle_slug or (has_listicle_title and has_numbered_h2s):
        return "Listicle"

    # ── How-to ────────────────────────────────────────────────────────────────
    title_words_set = set(title.split())
    h1_words_set = set(h1.split())
    has_howto_title = bool(
        title_words_set & _HOWTO_TITLE_WORDS or h1_words_set & _HOWTO_TITLE_WORDS
    )
    has_howto_slug = bool(_HOWTO_SLUG_PATTERNS.search(path))
    has_step_h2s = bool(re.search(r"(?:^|\| )(?:step|stage)\s+\d+", h2))
    if has_howto_slug or (has_howto_title and (has_step_h2s or has_numbered_h2s)):
        return "How-to"

    return None


def enrich_content_types(pages: list[PageRow], log: LogFn = print) -> list[PageRow]:
    """Assign content_type to all pages. Called after metadata crawl."""
    n = 0
    for p in pages:
        ct = classify_content_type(p)
        p.content_type = ct
        if ct:
            n += 1
    if n:
        log(f"▸ Content types: {n} pages classified (Listicle / How-to / Comparison).")
    return pages

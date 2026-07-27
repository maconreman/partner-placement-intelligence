"""
rank.py — Composite rank. Port of rank.ts.
Hard rules:
  D2: Composite score computed BEFORE deduplication.
  M3.5: +0.5 boost for Listicle/How-to/Comparison (stacks with blog +0.5).
"""
from __future__ import annotations
from .config import (
    COMPOSITE_WEIGHT_RELEVANCE, COMPOSITE_WEIGHT_SEO, TIER_PRIORITY, TIER_STRONG,
    SCORE_MIN, SCORE_MAX, EXPORT_LABELS,
)
from .util import CandidateRow, ResultRow, clamp_round1

TIER_ORDER = {"Priority": 0, "Strong": 1, "Monitor": 2}
CONTENT_TYPE_BOOST = 0.5
BLOG_BOOST = 0.5


def _tier_label(score: float) -> str:
    if score >= TIER_PRIORITY:
        return "Priority"
    if score >= TIER_STRONG:
        return "Strong"
    return "Monitor"


def build_results(cand: list[CandidateRow]) -> list[ResultRow]:
    # Clamp scores
    for c in cand:
        if c.topical_relevance_score is not None:
            c.topical_relevance_score = min(max(c.topical_relevance_score, SCORE_MIN), SCORE_MAX)
        if c.seo_score is not None:
            c.seo_score = min(max(c.seo_score, SCORE_MIN), SCORE_MAX)

    # Precision floor
    scored = [c for c in cand if (c.topical_relevance_score or 0) > 0.0]
    if not scored:
        return []

    # D2: composite BEFORE dedup
    def _composite(c: CandidateRow) -> float:
        base = (
            (c.topical_relevance_score or 0) * COMPOSITE_WEIGHT_RELEVANCE
            + (c.seo_score or 0) * COMPOSITE_WEIGHT_SEO
        )
        # Blog boost
        if c.page_category == "Blog":
            base += BLOG_BOOST
        # M3.5: content-type boost (stacks with blog boost)
        if c.content_type in ("Listicle", "How-to", "Comparison"):
            base += CONTENT_TYPE_BOOST
        return base

    with_composite = [(c, _composite(c)) for c in scored]
    with_composite.sort(key=lambda x: -x[1])

    # Dedup — keep first (best composite) per page
    seen: set[str] = set()
    deduped: list[tuple[CandidateRow, float]] = []
    for c, cs in with_composite:
        if c.page in seen:
            continue
        seen.add(c.page)
        deduped.append((c, cs))

    # Tier + blog tiebreaker sort
    results: list[ResultRow] = []
    for c, cs in deduped:
        results.append(ResultRow(
            account=c.account, domain=c.domain, page=c.page,
            page_category=c.page_category,
            clicks=c.clicks, impressions=c.impressions, position=c.position,
            query=c.query, query_all=c.query_all,
            seo_score=c.seo_score,
            meta_title=c.meta_title, meta_description=c.meta_description,
            h1=c.h1, h2=c.h2, content_type=c.content_type,
            matched_on=c.matched_on, anchor_text=c.anchor_text,
            anchor_source=c.anchor_source, lexical_score=c.lexical_score,
            topical_relevance_score=c.topical_relevance_score,
            composite_score=cs,
            tier_label=_tier_label(c.topical_relevance_score or 0),
            is_blog_flag=0 if c.page_category == "Blog" else 1,
            rank=0,
        ))

    results.sort(key=lambda r: (
        TIER_ORDER.get(r.tier_label, 2),
        r.is_blog_flag,
        -r.composite_score,
    ))
    for i, r in enumerate(results):
        r.rank = i + 1

    return results


def to_export_rows(results: list[ResultRow]) -> list[dict]:
    return [
        {
            EXPORT_LABELS["rank"]: r.rank,
            "Page": r.page,
            "Matched On": r.matched_on or "",
            "Anchor Text": r.anchor_text or "",
            EXPORT_LABELS["relevance"]: r.topical_relevance_score or 0,
            EXPORT_LABELS["seo"]: r.seo_score or 0,
            EXPORT_LABELS["type"]: "" if r.page_category == "Other" else r.page_category,
            EXPORT_LABELS["content_type"]: r.content_type or "",
            EXPORT_LABELS["title"]: r.meta_title or "",
            EXPORT_LABELS["metaDesc"]: r.meta_description or "",
            EXPORT_LABELS["h1"]: r.h1 or "",
            EXPORT_LABELS["h2"]: r.h2 or "",
            "Top Query": r.query or "",
            "Clicks": r.clicks,
            "Impressions": r.impressions,
            "Position": r.position,
        }
        for r in results
    ]

"""
aggregate.py — Port of aggregate.ts.
Collapse GSC query rows to unique pages, compute SEO score.
"""
from __future__ import annotations
from .config import SEO_WEIGHT_CLICKS, SEO_WEIGHT_IMPRESSIONS, SEO_WEIGHT_POSITION, SCORE_MIN, SCORE_MAX
from .classify import classify_page
from .util import GscRow, PageRow, percentile_norm, clamp_round1

QUERY_BAG_CAP = 50


def aggregate_to_pages(rows: list[GscRow]) -> list[PageRow]:
    if not rows:
        return []

    groups: dict[str, dict] = {}
    for r in rows:
        key = f"{r.account}|{r.domain}|{r.page}"
        if key not in groups:
            groups[key] = {
                "account": r.account, "domain": r.domain, "page": r.page,
                "page_category": classify_page(r.page),
                "clicks": 0, "impressions": 0, "pos_x_imp": 0.0, "rows": [],
            }
        g = groups[key]
        g["clicks"] += r.clicks
        g["impressions"] += r.impressions
        g["pos_x_imp"] += r.position * r.impressions
        g["rows"].append(r)

    out: list[PageRow] = []
    for g in groups.values():
        position = round(g["pos_x_imp"] / g["impressions"], 2) if g["impressions"] > 0 else 0.0
        ordered = sorted(g["rows"], key=lambda r: -r.clicks)
        top_query = str(ordered[0].query) if ordered else ""

        seen: list[str] = []
        for r in ordered:
            q = str(r.query or "")
            if q and q not in seen:
                seen.append(q)
            if len(seen) >= QUERY_BAG_CAP:
                break

        out.append(PageRow(
            account=g["account"], domain=g["domain"], page=g["page"],
            page_category=g["page_category"],
            clicks=g["clicks"], impressions=g["impressions"], position=position,
            query=top_query, query_all=" | ".join(seen),
        ))
    return out


def compute_seo_score(pages: list[PageRow]) -> list[float]:
    clicks_pct = percentile_norm([p.clicks for p in pages])
    impress_pct = percentile_norm([p.impressions for p in pages])
    pos_pct_raw = percentile_norm([p.position for p in pages])
    pos_pct = [1.0 - v for v in pos_pct_raw]  # lower position is better

    return [
        clamp_round1(
            (clicks_pct[i] * SEO_WEIGHT_CLICKS
             + impress_pct[i] * SEO_WEIGHT_IMPRESSIONS
             + pos_pct[i] * SEO_WEIGHT_POSITION) * 10,
            SCORE_MIN, SCORE_MAX
        )
        for i in range(len(pages))
    ]

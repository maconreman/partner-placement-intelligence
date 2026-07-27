"""
quickmatch.py — Lexical pre-filter. Port of quickmatch.ts.
Hard rules preserved:
  D4: Gate on best single surface (surface_max >= gate_min), never cross-surface sum.
  D9: _DOMAIN_GENERICS filtered alongside GENERIC_TOKENS.
"""
from __future__ import annotations
import re
from .config import NLP_SHORTLIST_CAP, _DOMAIN_GENERICS
from .util import PageRow, CandidateRow, LogFn, url_path

GENERIC_TOKENS = {
    "software", "platform", "platforms", "tool", "tools", "service", "services",
    "solution", "solutions", "system", "systems", "app", "apps", "online",
    "best", "top", "company", "companies", "provider", "providers", "management",
}
MATCH_STOPWORDS = {"the", "and", "for", "with", "your", "you", "our", "from", "that", "this", "are"}
STOP_SLUGS = {"www", "com", "org", "net", "co", "html", "php", "aspx", "index"}

MATCH_CASCADE = [
    ("Slug", "slug"),
    ("Meta+Title", "meta"),
    ("H1", "h1"),
    ("H2", "h2"),
    ("Query", "query_all"),
]


def _slug_text(url: str) -> str:
    path = url_path(url)
    return " ".join(t for t in re.split(r"[-_/]", path) if t and t not in STOP_SLUGS)


def _sig_score(text: str, topic: str, valid_bigrams: list[str], token_res: list[re.Pattern]) -> int:
    if not text:
        return 0
    s = str(text).lower()
    sc = 0
    if topic and topic in s:
        sc += 3
    for bg in valid_bigrams:
        if bg and bg in s:
            sc += 2
    for rx in token_res:
        if rx.search(s):
            sc += 1
    return sc


def _best_fragment(text: str, topic: str, valid_bigrams: list[str], token_res: list[re.Pattern]) -> tuple[int, str]:
    if not text:
        return 0, ""
    fragments = [f.strip() for f in str(text).split("|") if f.strip()] or [str(text).strip()]
    best_sc, best_frag = 0, ""
    for f in fragments:
        sc = _sig_score(f, topic, valid_bigrams, token_res)
        if sc > best_sc:
            best_sc, best_frag = sc, f
    return best_sc, best_frag


def _surface_value(p: PageRow, surface: str) -> str:
    if surface == "slug":
        return _slug_text(p.page)
    if surface == "meta":
        return f"{p.meta_title or ''} | {p.meta_description or ''}"
    if surface == "h1":
        return p.h1 or ""
    if surface == "h2":
        return p.h2 or ""
    if surface == "query_all":
        return p.query_all or ""
    return ""


def quick_match_candidates(
    pages: list[PageRow],
    raw_topic: str,
    cap: int = NLP_SHORTLIST_CAP,
    log: LogFn = print,
) -> list[CandidateRow]:
    if not pages:
        return []

    # Exclude Hub pages; Programmatic pages stay (D4/D8)
    working = [p for p in pages if p.page_category != "Hub"]
    if not working:
        return []

    topic = raw_topic.lower().strip()
    raw_tokens = re.findall(r"[a-z0-9]+", topic)

    distinct = [
        t for t in raw_tokens
        if len(t) > 2 and t not in GENERIC_TOKENS and t not in _DOMAIN_GENERICS and t not in MATCH_STOPWORDS
    ]
    if not distinct:
        distinct = [t for t in raw_tokens if len(t) > 1 and t not in _DOMAIN_GENERICS]
    if not distinct:
        distinct = [t for t in raw_tokens if len(t) > 1]

    bigrams = [f"{raw_tokens[i]} {raw_tokens[i+1]}" for i in range(len(raw_tokens) - 1)]
    valid_bigrams = [bg for bg in bigrams if any(dt in bg for dt in distinct)]
    token_res = [re.compile(r"\b" + re.escape(t) + r"\b") for t in distinct]

    gate_min = 2 if len(distinct) >= 2 else 1

    scored: list[dict] = []
    for p in working:
        labels: list[str] = []
        anchors: list[str] = []
        total = 0
        best = 0
        best_anchor_sc = 0
        best_anchor_surface = ""

        for label, surface in MATCH_CASCADE:
            sc, frag = _best_fragment(_surface_value(p, surface), topic, valid_bigrams, token_res)
            total += sc
            if sc > best:
                best = sc
            if sc >= gate_min:
                labels.append(label)
                if frag:
                    anchors.append(frag)
                if sc > best_anchor_sc and frag:
                    best_anchor_sc = sc
                    best_anchor_surface = surface

        scored.append({
            "page": p, "matched_on": " | ".join(labels),
            "anchor_text": " | ".join(a for a in anchors if a),
            "anchor_source": best_anchor_surface,
            "lexical_score": total, "surface_max": best,
        })

    # D4: gate on surface_max (single-surface), not total
    qualified = [s for s in scored if s["surface_max"] >= gate_min]
    qualified.sort(key=lambda s: (-s["lexical_score"], -(s["page"].seo_score or 0)))
    qualified = qualified[:cap]

    out: list[CandidateRow] = []
    for s in qualified:
        p = s["page"]
        out.append(CandidateRow(
            account=p.account, domain=p.domain, page=p.page,
            page_category=p.page_category,
            clicks=p.clicks, impressions=p.impressions, position=p.position,
            query=p.query, query_all=p.query_all,
            seo_score=p.seo_score,
            meta_title=p.meta_title, meta_description=p.meta_description,
            h1=p.h1, h2=p.h2, content_type=p.content_type,
            matched_on=s["matched_on"],
            anchor_text=s["anchor_text"],
            anchor_source=s["anchor_source"],
            lexical_score=s["lexical_score"],
        ))

    log(f"▸ Quick match: {len(out)} of {len(working)} unique pages qualified.")
    return out

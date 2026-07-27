"""
relevance.py — HF bi-encoder relevance scoring. Port of relevance.ts.
D1: NO TF-IDF fallback. HFAPIError is raised on any failure.
"""
from __future__ import annotations
import asyncio
import re
import httpx
from typing import Optional

from .config import (
    HF_API_TOKEN, HF_EMBED_MODEL, HARD_NEGATIVES, _DRIFT_NEGATIVES,
    CONTRASTIVE_WEIGHT, NLP_BATCH_SIZE, NLP_EMBED_WORKERS,
    RELEVANCE_MIN_THRESHOLD, QUERY_ONLY_MIN_THRESHOLD, SCORE_MIN, SCORE_MAX,
)
from .util import CandidateRow, LogFn, l2normalize, cosine_to_query, clamp_round1, map_with_concurrency

EMBED_URL = f"https://router.huggingface.co/hf-inference/models/{HF_EMBED_MODEL}/pipeline/feature-extraction"

GENERIC_TOKENS = {
    "software", "platform", "platforms", "tool", "tools", "service", "services",
    "solution", "solutions", "system", "systems", "app", "apps", "online",
    "best", "top", "company", "companies", "provider", "providers", "management",
}
MATCH_STOPWORDS = {"the", "and", "for", "with", "your", "you", "our", "from", "that", "this", "are"}


class HFAPIError(Exception):
    pass


async def _post_embed(texts: list[str], timeout_s: float) -> list[list[float]]:
    if not HF_API_TOKEN or not HF_API_TOKEN.startswith("hf_"):
        raise HFAPIError("HF_API_TOKEN is missing or invalid.")
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(
            EMBED_URL,
            headers={"Authorization": f"Bearer {HF_API_TOKEN}", "Content-Type": "application/json"},
            json={"inputs": texts},
        )
        if resp.status_code != 200:
            body = resp.text[:200]
            raise HFAPIError(f"Embedding returned HTTP {resp.status_code}: {body}")
        return resp.json()


async def warmup_embed_model() -> None:
    try:
        await _post_embed(["warmup"], 30)
    except Exception:
        pass


def _build_nlp_corpus(rows: list[CandidateRow], raw_topic: str) -> list[str]:
    topic_terms = [t for t in re.findall(r"[a-z0-9]+", raw_topic.lower()) if len(t) > 2]
    corpus = []
    for r in rows:
        bits = [
            str(r.query or r.query_all or ""),
            str(r.meta_title or ""),
            str(r.h1 or ""),
        ]
        for col in [r.h2 or "", r.meta_description or ""]:
            val = str(col)
            if not val:
                continue
            fragments = [f.strip() for f in re.split(r"[|•♦\n.\-]", val) if f.strip()]
            for frag in fragments:
                if any(term in frag.lower() for term in topic_terms):
                    bits.append(frag)
        corpus.append(" ".join(bits))
    return corpus


async def compute_relevance_scores(
    rows: list[CandidateRow],
    raw_topic: str,
    log: LogFn = print,
) -> list[float]:
    if not rows:
        return []
    if not HF_API_TOKEN or not HF_API_TOKEN.startswith("hf_"):
        raise HFAPIError("HF_API_TOKEN is missing or invalid.")

    corpus = _build_nlp_corpus(rows, raw_topic)

    # ── Dynamic collision check (D9-adjacent) ─────────────────────────────────
    raw_tokens = re.findall(r"[a-z0-9]+", raw_topic.lower().strip())
    topic_distinct = {t for t in raw_tokens if len(t) > 2 and t not in GENERIC_TOKENS and t not in MATCH_STOPWORDS}
    if not topic_distinct:
        topic_distinct = {t for t in raw_tokens if len(t) > 1}

    active_negatives: list[str] = []
    for neg in HARD_NEGATIVES + _DRIFT_NEGATIVES:
        neg_tokens = re.findall(r"[a-z0-9]+", neg.lower())
        neg_distinct = [t for t in neg_tokens if len(t) > 2 and t not in GENERIC_TOKENS and t not in MATCH_STOPWORDS]
        collision = any(
            td == nt or (len(td) >= 3 and (td.startswith(nt) or nt.startswith(td)))
            for td in topic_distinct for nt in neg_distinct
        )
        if not collision:
            active_negatives.append(neg)
    negatives = active_negatives if active_negatives else ["sorority", "summer camp", "payroll software"]

    # ── Contrastive query embedding ───────────────────────────────────────────
    try:
        q_prefix = "Represent this sentence for searching relevant passages: "
        try:
            embs = await _post_embed([q_prefix + raw_topic] + negatives, 60)
        except HFAPIError as e:
            if "503" in str(e):
                log("▸ System warming up — retrying in 15 seconds...")
                await asyncio.sleep(15)
                embs = await _post_embed([q_prefix + raw_topic] + negatives, 60)
            else:
                raise

        dim = len(embs[0])
        neg_mean = [0.0] * dim
        for emb in embs[1:]:
            for d in range(dim):
                neg_mean[d] += emb[d]
        for d in range(dim):
            neg_mean[d] /= len(embs) - 1

        q = [embs[0][d] - CONTRASTIVE_WEIGHT * neg_mean[d] for d in range(dim)]
        contrastive_q = l2normalize(q)
    except HFAPIError:
        raise
    except Exception as exc:
        raise HFAPIError(f"Relevance scoring failed: {exc}") from exc

    # ── Batched corpus embedding ──────────────────────────────────────────────
    try:
        batches = [corpus[i:i + NLP_BATCH_SIZE] for i in range(0, len(corpus), NLP_BATCH_SIZE)]

        async def embed_batch(batch: list[str]) -> list[list[float]]:
            return await _post_embed(batch, 90)

        batch_results = await map_with_concurrency(batches, NLP_EMBED_WORKERS, embed_batch)
        mat: list[list[float]] = []
        for b in batch_results:
            mat.extend(b)

        raw_scores = cosine_to_query(contrastive_q, mat)
    except HFAPIError:
        raise
    except Exception as exc:
        raise HFAPIError(f"Relevance scoring failed: {exc}") from exc

    # ── Query-only precedence penalty + zero-out floor ────────────────────────
    return [
        (lambda threshold, score: 0.0 if score < threshold else score)(
            QUERY_ONLY_MIN_THRESHOLD if rows[i].matched_on == "Query" else RELEVANCE_MIN_THRESHOLD,
            clamp_round1(raw_scores[i] * 10, SCORE_MIN, SCORE_MAX),
        )
        for i in range(len(rows))
    ]

"""
relevance.py — bi-encoder relevance scoring. Port of relevance.ts.
D1: NO TF-IDF fallback. HFAPIError is raised on any failure.
M9.4 (D-M9-19): embeddings run locally inside the Space with
sentence-transformers (same model, BAAI/bge-base-en-v1.5, same pooling and
normalisation as the HF Inference feature-extraction endpoint), so scoring
no longer spends Inference Provider credits. The HTTP router call is gone.
The name HFAPIError is kept so pipeline.py needs no change.
"""
from __future__ import annotations
import asyncio
import re
import threading
from typing import Any, Optional

from .config import (
    HF_EMBED_MODEL, HARD_NEGATIVES, _DRIFT_NEGATIVES,
    CONTRASTIVE_WEIGHT, NLP_BATCH_SIZE, NLP_EMBED_WORKERS,
    RELEVANCE_MIN_THRESHOLD, QUERY_ONLY_MIN_THRESHOLD, SCORE_MIN, SCORE_MAX,
)
from .util import CandidateRow, LogFn, l2normalize, cosine_to_query, clamp_round1, map_with_concurrency

# Texts per forward pass on CPU. Independent of NLP_BATCH_SIZE (which only
# controls how the corpus is chunked before it reaches _post_embed).
LOCAL_ENCODE_BATCH = 32

GENERIC_TOKENS = {
    "software", "platform", "platforms", "tool", "tools", "service", "services",
    "solution", "solutions", "system", "systems", "app", "apps", "online",
    "best", "top", "company", "companies", "provider", "providers", "management",
}
MATCH_STOPWORDS = {"the", "and", "for", "with", "your", "you", "our", "from", "that", "this", "are"}


class HFAPIError(Exception):
    pass


# ── Local model (loaded once per process, shared by all users) ────────────────
_model: Optional[Any] = None
# One lock guards both the lazy load and encode(): torch already uses every
# CPU core per call, so concurrent encodes from several users would only
# thrash. Requests queue here instead; results are identical.
_model_lock = threading.Lock()


def _model_loaded() -> bool:
    return _model is not None


def _get_model() -> Any:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # heavy import, deferred
        _model = SentenceTransformer(HF_EMBED_MODEL, device="cpu")
    return _model


def _encode_sync(texts: list[str]) -> list[list[float]]:
    with _model_lock:
        model = _get_model()
        vecs = model.encode(
            texts,
            batch_size=LOCAL_ENCODE_BATCH,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    return vecs.tolist()


async def _post_embed(texts: list[str], timeout_s: float) -> list[list[float]]:
    """Same signature as the old HTTP version. timeout_s is kept for
    compatibility; local inference is not cancelled mid-batch."""
    try:
        return await asyncio.to_thread(_encode_sync, texts)
    except Exception as exc:
        raise HFAPIError(f"Local embedding failed: {exc}") from exc


async def warmup_embed_model() -> None:
    """Loads the model off the event loop so the first scoring run is fast."""
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
        if not _model_loaded():
            log("Loading the embedding model (first run after a restart takes ~20 seconds).")
        embs = await _post_embed([q_prefix + raw_topic] + negatives, 60)

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

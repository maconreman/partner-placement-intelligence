// ─────────────────────────────────────────────────────────────────────────────
// relevance.ts  —  Port of compute_relevance_scores() + corpus build (CELL 4)
//
// Level 2 contrastive bi-encoder relevance (BAAI/bge-base-en-v1.5 via the HF
// router). Hard rule preserved: NO TF-IDF fallback. Any HF failure throws
// HFAPIError so the UI surfaces a "Try again" state — it never silently degrades.
//
// Logic preserved verbatim:
//   • Surgical corpus: query/title/H1 + topic-bearing H2/meta fragments only.
//   • Dynamic collision check drops hard negatives that share a topic token.
//   • Contrastive query = topic_emb − 0.15 · mean(active_negative_embs), renormed.
//   • Query-only matches use the escalated 6.5 floor; others use 5.0. Below → 0.
// ─────────────────────────────────────────────────────────────────────────────
import {
  HF_API_TOKEN, HF_EMBED_MODEL, HARD_NEGATIVES, _DRIFT_NEGATIVES, CONTRASTIVE_WEIGHT,
  NLP_BATCH_SIZE, NLP_EMBED_WORKERS, RELEVANCE_MIN_THRESHOLD, QUERY_ONLY_MIN_THRESHOLD,
  SCORE_MIN, SCORE_MAX,
} from "./config";
import {
  CandidateRow, LogFn, l2normalize, cosineToQuery, clampRound1, mapWithConcurrency, sleep,
} from "./util";

export class HFAPIError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HFAPIError";
  }
}

const GENERIC_TOKENS = new Set([
  "software", "platform", "platforms", "tool", "tools", "service", "services",
  "solution", "solutions", "system", "systems", "app", "apps", "online",
  "best", "top", "company", "companies", "provider", "providers", "management",
]);
const MATCH_STOPWORDS = new Set(["the", "and", "for", "with", "your", "you", "our", "from", "that", "this", "are"]);

const EMBED_URL = `https://router.huggingface.co/hf-inference/models/${HF_EMBED_MODEL}/pipeline/feature-extraction`;

async function postEmbed(texts: string[], timeoutMs: number): Promise<Response> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(EMBED_URL, {
      method: "POST",
      headers: { Authorization: `Bearer ${HF_API_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ inputs: texts }),
      signal: ctrl.signal,
    });
  } finally {
    clearTimeout(t);
  }
}

export async function warmupEmbedModel(): Promise<void> {
  try {
    await postEmbed(["warmup"], 30000);
  } catch {
    /* fire and forget */
  }
}

function buildNlpCorpus(rows: CandidateRow[], rawTopic: string): string[] {
  const topicTerms = (rawTopic.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((t) => t.length > 2);
  return rows.map((r) => {
    const bits = [String(r.query ?? r.query_all ?? ""), String(r.meta_title ?? ""), String(r.h1 ?? "")];
    for (const col of [r.h2 ?? "", r.meta_description ?? ""]) {
      const val = String(col);
      if (!val) continue;
      const fragments = val.split(/[|•♦\n.\-]/).map((f) => f.trim()).filter(Boolean);
      for (const frag of fragments) {
        if (topicTerms.some((term) => frag.toLowerCase().includes(term))) bits.push(frag);
      }
    }
    return bits.join(" ");
  });
}

export async function computeRelevanceScores(
  rows: CandidateRow[],
  rawTopic: string,
  log: LogFn = console.log
): Promise<number[]> {
  if (!rows.length) return [];
  if (!HF_API_TOKEN || !HF_API_TOKEN.startsWith("hf_")) {
    throw new HFAPIError("HF_API_TOKEN is missing or invalid.");
  }

  const corpus = buildNlpCorpus(rows, rawTopic);

  // ── Dynamic collision check ────────────────────────────────────────────────
  const rawTokens = (rawTopic.toLowerCase().trim().match(/[a-z0-9]+/g) ?? []);
  let topicDistinct = new Set(rawTokens.filter((t) => t.length > 2 && !GENERIC_TOKENS.has(t) && !MATCH_STOPWORDS.has(t)));
  if (!topicDistinct.size) topicDistinct = new Set(rawTokens.filter((t) => t.length > 1));

  const activeNegatives: string[] = [];
  for (const neg of [...HARD_NEGATIVES, ..._DRIFT_NEGATIVES]) {
    const negTokens = neg.toLowerCase().match(/[a-z0-9]+/g) ?? [];
    const negDistinct = negTokens.filter((t) => t.length > 2 && !GENERIC_TOKENS.has(t) && !MATCH_STOPWORDS.has(t));
    let collision = false;
    for (const td of topicDistinct) {
      for (const nt of negDistinct) {
        if (td === nt || (td.length >= 3 && (td.startsWith(nt) || nt.startsWith(td)))) {
          collision = true;
          break;
        }
      }
      if (collision) break;
    }
    if (!collision) activeNegatives.push(neg);
  }
  const negatives = activeNegatives.length ? activeNegatives : ["sorority", "summer camp", "payroll software"];

  let contrastiveQ: number[];
  try {
    const qPrefix = "Represent this sentence for searching relevant passages: ";
    let respNeg = await postEmbed([qPrefix + rawTopic, ...negatives], 60000);
    if (respNeg.status === 503) {
      log("▸ System warming up — retrying in 15 seconds...");
      await sleep(15000);
      respNeg = await postEmbed([qPrefix + rawTopic, ...negatives], 60000);
    }
    if (respNeg.status !== 200) {
      const body = (await respNeg.text()).slice(0, 200);
      throw new HFAPIError(`Pass 1 embedding returned HTTP ${respNeg.status}: ${body}`);
    }

    const embs: number[][] = await respNeg.json();
    const dim = embs[0].length;
    const negMean = new Array(dim).fill(0);
    for (let i = 1; i < embs.length; i++) for (let d = 0; d < dim; d++) negMean[d] += embs[i][d];
    for (let d = 0; d < dim; d++) negMean[d] /= (embs.length - 1);

    const q = embs[0].map((v, d) => v - CONTRASTIVE_WEIGHT * negMean[d]);
    contrastiveQ = l2normalize(q);
  } catch (exc) {
    if (exc instanceof HFAPIError) throw exc;
    throw new HFAPIError(`Relevance scoring failed: ${exc instanceof Error ? exc.message : String(exc)}`);
  }

  // ── Batched corpus embedding ───────────────────────────────────────────────
  let finalScores: number[];
  try {
    const batches: string[][] = [];
    for (let i = 0; i < corpus.length; i += NLP_BATCH_SIZE) batches.push(corpus.slice(i, i + NLP_BATCH_SIZE));

    const batchResults = await mapWithConcurrency(batches, NLP_EMBED_WORKERS, async (batch) => {
      const r = await postEmbed(batch, 90000);
      if (r.status !== 200) {
        const body = (await r.text()).slice(0, 200);
        throw new HFAPIError(`Batch embedding failed HTTP ${r.status}: ${body}`);
      }
      return (await r.json()) as number[][];
    });

    const mat: number[][] = [];
    for (const b of batchResults) mat.push(...b);
    finalScores = cosineToQuery(contrastiveQ, mat);
  } catch (exc) {
    if (exc instanceof HFAPIError) throw exc;
    throw new HFAPIError(`Relevance scoring failed: ${exc instanceof Error ? exc.message : String(exc)}`);
  }

  // ── Query-only precedence penalty + zero-out floor ─────────────────────────
  return rows.map((r, i) => {
    const threshold = r.matched_on === "Query" ? QUERY_ONLY_MIN_THRESHOLD : RELEVANCE_MIN_THRESHOLD;
    const score = clampRound1(finalScores[i] * 10, SCORE_MIN, SCORE_MAX);
    return score < threshold ? 0.0 : score;
  });
}

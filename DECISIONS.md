# Nexus Placement Intelligence — Architectural Decisions
**Alpha M7 · FastAPI + Next.js (static export) · Hugging Face Spaces (Docker)**

Non-negotiable rules for anyone modifying this codebase. Each decision records
what the rule is, which files it lives in, why it exists, and what silently breaks
if it is violated. A coding assistant that skips this file will likely introduce
a silent regression.

M7 additions are marked **[M7]**. Prior decisions D1–D15 and D-S1–D-S5 are
unchanged unless noted.

---

## D1 — No TF-IDF fallback, ever

**Rule:** When the HuggingFace Inference API fails for any reason, the relevance
scoring stage must raise `HFAPIError`. The UI shows a "Try again" / "Change topic"
prompt. Silent degradation to TF-IDF cosine similarity is explicitly forbidden.

**Where it lives:**
- `relevance.py` — `HFAPIError` class + raise on any non-200 response
- `pipeline.py` — catches only `HFAPIError`, emits `{ type: "error", code: "hf_error" }`

**Why:** TF-IDF operates on raw term overlap and matches unrelated pages that share
generic SEO vocabulary. The bi-encoder contrastive scoring is the entire precision
guarantee. A TF-IDF fallback produces results that look plausible but are
meaningfully wrong — the user cannot tell the difference.

**What breaks if violated:** Partnerships team exports results that appear scored
but are keyword-overlap matches. Placements are wrong; clients are mis-served.

---

## D2 — Composite score before deduplication

**Rule:** Compute `composite_score` (70% relevance + 30% SEO + any active boosts) on
the full candidate set, then deduplicate by page URL. Never the reverse.

**Where it lives:** `rank.py` — `build_results()` computes composite for every
candidate row, sorts descending, then keeps the first occurrence per page URL.

**Why:** GSC returns multiple rows per page (one per query). Different queries for
the same page produce different relevance scores. Deduping before scoring keeps an
arbitrary row; deduping after keeps the query that produced the best placement signal.

**What breaks if violated:** Pages are represented by the wrong query in exports.
Scores are understated for pages whose best signal comes from a non-top query.

---

## D3 — Layer 3 root-inheritance guard

**Rule:** In `_parent_key()`, when a URL has fewer than 2 path segments, return
`None`. Do not let top-level pages inherit a category from the domain root.

**Where it lives:** `classify.py` — `_parent_key()`:
```python
if len(segs) < 2:
    return None  # top-level pages never inherit
```

**Why:** The domain root is not a content category. Without the guard, every
top-level page (`/about`, `/pricing`, `/blog`) inherits the most common category
on the site, which on FFG domains is "Blog" or "Product/Service".

**What breaks if violated:** Homepages, pricing pages, and contact pages are tagged
as Blog or Product/Service. The Level 0 crawl filter may not fire correctly for
mis-tagged pages.

---

## D4 — Single-surface bigram gate for quick-match

**Rule:** A page qualifies only if its best single-surface score (`surface_max`)
meets the gate threshold. Do not sum scores across surfaces.

**Where it lives:** `quickmatch.py` — `quick_match_candidates()`:
```python
qualified = [s for s in scored if s["surface_max"] >= gate_min]
```

**Why:** Cross-surface accumulation was the primary false-positive failure mode in
M3.1/M3.2. A page could score 1 on slug + 1 on H1 + 1 on query — none a meaningful
match, but a sum of 3 passed the gate. Single-surface ensures a genuine bigram or
phrase match appears within one content signal.

**What breaks if violated:** Candidate counts spike 10x. HF batch costs increase.
Result quality degrades because the zero-out floor becomes the only filter.

---

## D5 — GCP OAuth client must be type "Web application"

**Rule:** The app requires a **Web application** OAuth 2.0 client in GCP, not Desktop.
All redirect URIs must be registered.

**Where it lives:** `googleauth.py` — `redirect_uri()` returns
`{APP_BASE_URL}/api/auth/callback`.

**Why:** Desktop clients use `urn:ietf:wg:oauth:2.0:oob` or `localhost`. A deployed
server cannot open a browser or listen on localhost.

**Setup:**
1. GCP → APIs & Services → Credentials → Create OAuth client ID
2. Application type: **Web application**
3. Authorized redirect URIs:
   - `http://localhost:7860/api/auth/callback` (dev)
   - `https://nexus-placement-intelligence.hf.space/api/auth/callback` (prod)

**What breaks if violated:** Google rejects the redirect URI with
`redirect_uri_mismatch` or `invalid_client`.

---

## D6 — ~~Vercel Pro plan required~~ — RETIRED (M3.5)

No longer applicable. The M3.5 migration to HF Spaces eliminated the 300-second Vercel
constraint. M7 confirms: the Vercel 60-second ceiling was the most likely cause of
"hours" behavior on 100+ account runs — it silently killed pipelines and mid-insert BQ
writes. HF Spaces has no timeout ceiling.

---

## D7 — `pandas itertuples()._asdict()` renames leading-underscore columns

**Scope:** Colab/pandas only. The FastAPI backend uses Python dataclasses — this bug
cannot occur there. Retained as a historical record for Colab notebook maintenance.

---

## D8 — Programmatic series pages are kept, not dropped

**Rule:** When `detect_programmatic_series()` identifies a URL pattern with ≥10
"Other" children, those pages are re-tagged "Other" and kept in results, not excluded.

**Where it lives:** `classify.py` — `detect_programmatic_series()`

**Why:** Leadership directive. Programmatic series pages often have real organic
traffic and represent legitimate placement opportunities.

**What breaks if violated:** High-traffic subfolder pages silently disappear from
results.

---

## D9 — `_DOMAIN_GENERICS` must be filtered alongside `GENERIC_TOKENS`

**Rule:** In quick-match token extraction, filter both `GENERIC_TOKENS` and
`_DOMAIN_GENERICS` (fundraising, nonprofit, donation, donor, etc.).

**Where it lives:** `quickmatch.py` + `config.py`

**Why:** "nonprofit" is present on virtually every FFG domain page. Without filtering
it, the quick-match stage floods with false candidates on every FFG domain run.

**What breaks if violated:** Candidate counts spike. HF API costs increase. Result
quality drops.

---

## D10 — Token refresh must write back to Upstash

**Rule:** When `build_authed_credentials()` refreshes an expired access token, the
new token must be written back to Upstash immediately.

**Where it lives:** `googleauth.py` — after `creds.refresh(request)`.

**Why:** HF Spaces may restart the container at any time — credentials cannot live in
memory. Remove the write-back and tokens expire after one hour with no clear symptom.

**What breaks if violated:** Tool works for the first hour after consent, then every
GSC/Drive call silently returns 401.

---

## D11 — BigQuery DATE partition column requires a typed date value, not a plain string

**Rule:** When inserting rows into `gsc_data`, the `date` field must be a native
`datetime.date` object. Passing a plain ISO string causes BigQuery to silently skip
every row on a DATE partition column — the insert call returns success but writes
nothing.

**Where it lives:** `bigquery.py` — `sync_to_bigquery()`:
```python
monday = date.fromisoformat(_week_monday())
"date": monday,
```

**Why this is a trap:** `page_metadata.snapshot_date` and `feedback.submitted_at` are
not partition keys, so BigQuery accepts plain strings there. The asymmetry caused all
milestones prior to M7 to appear to write successfully while `gsc_data` stayed empty.

**What breaks if violated:** GSC rows are never written. Log says "Warmed warehouse
with N rows" but the table stays empty. Every run does a live GSC fetch.

---

## D12 — Freshness check gates the BigQuery short-circuit return

**Rule:** `fetch_all_domains()` only returns early when the warehouse is fresh —
newest `date` >= current week's Monday. A stale warehouse falls through to live GSC.

**Where it lives:** `gsc.py` — `fetch_all_domains()`.

**Why:** Without the freshness check the warehouse populates on the first run and
never updates again — every subsequent run short-circuits on stale data.

**What breaks if violated:** GSC data stops refreshing. Log shows "Warehouse has N
rows" forever with no indication data is stale.

---

## D13 — Google Sheets cache is retired; do not re-introduce it

**Rule:** The Google Sheets cache layer is permanently removed. BigQuery is the sole
storage layer. If BigQuery is not provisioned, the tool runs live-only.

**What breaks if violated:** Freshness check reads from BigQuery and would never see
Sheets-only rows, causing live fetches on every run despite a warm cache.

---

## D14 — `.env` must be loaded before any local module import in `main.py`

**Rule:** `from dotenv import load_dotenv` and `load_dotenv()` must be the first two
executable lines in `main.py`, before any local import.

**Where it lives:** `main.py`:
```python
from dotenv import load_dotenv
load_dotenv()  # must precede all local imports

from pathlib import Path
from fastapi import FastAPI
# ... then local imports
```

**Why:** `config.py` reads env vars at module import time. If `load_dotenv()` runs
after those lines, `USE_BIGQUERY` is already `False` and `GOOGLE_CLIENT_ID` is empty.

**Bug history:** Confirmed production bug in M3.5. Symptom: `RuntimeError:
GOOGLE_CLIENT_ID is not set` on first auth request; admin sync returning 0 domains.

---

## D15 — Admin sync-gsc must call `fetch_all_domains_live()`, not `fetch_all_domains()`

**Rule:** The admin warehouse sync job must call `fetch_all_domains_live()` — which
bypasses the D12 freshness gate — rather than `fetch_all_domains()`.

**Where it lives:** `routers/admin.py` — `sync_gsc()`.

**Why:** Calling `fetch_all_domains()` in the admin sync job detects a fresh warehouse,
returns cached rows, and writes the same data back — writing nothing new.

**Bug history:** Confirmed production bug in M3.5.

---

## D-S1 — Level 0 filter is the unified exclusion gate

**Rule:** All page exclusion logic must live in the Level 0 filter inside `crawl.py`.
Do not scatter exclusion checks across pipeline stages.

**Why:** Scattered exclusion logic creates silent disagreements between stages — a page
excluded from the crawl but still present in quick-match candidates produces an empty
metadata row.

---

## D-S2 — Fresh flag must not gate the BigQuery writeback

**Rule:** The freshness check (D12) gates only the early-return from the read path.
It must never gate `sync_to_bigquery()`.

**Why:** Adding a freshness gate before writeback causes "Warehouse empty" on the first
run — no data yet → not fresh → no write → warehouse never populated.

---

## D-S3 — User-facing runs use `fetch_all_domains()`; admin sync uses `fetch_all_domains_live()`

**Rule:** `/api/run` calls `fetch_all_domains()` (respects D12). Admin sync calls
`fetch_all_domains_live()` (bypasses D12, always hits live GSC).

**Why:** This is the functional separation that makes the combined-app architecture
work — users get fast BigQuery reads; admin jobs handle slow live fetches.

---

## D-S4 — Metadata crawl uses 16KB partial fetch with `</head>` abort

**Rule:** The metadata crawl must use `Range: bytes=0-16383` and abort after `</head>`
is found. Do not fetch full page HTML.

**Why:** All needed metadata (title, meta description, H1, H2) is in `<head>` and the
first few hundred bytes of `<body>`. Full HTML fetch increases crawl time 3–5x.

---

## D-S5 — `list_gsc_properties()` exceptions must be logged, not swallowed

**Rule:** In `list_gsc_properties()`, the `except` block must log the exception.
Never use a bare `except Exception: pass`.

**Why:** The silent swallow was the proximate cause of the "0 domains" production bug
in M3.5 — it made a missing env var look like a permissions problem.

---

## D-M7-1 — App access gate is the sole access-control layer for human users [M7]

**Rule:** The gate (`ffg_gate` cookie, signed by `APP_GATE_SECRET`) is the only
mechanism controlling who can open the app. Do not re-introduce Basic Auth middleware.
The gate must be enforced in FastAPI middleware, not in individual route handlers.

**Where it lives:**
- `backend/lib/gate.py` — credential check + HMAC cookie issue/verify
- `backend/routers/gate.py` — `/api/gate/login`, `/api/gate/logout`, `/api/gate/status`
- `backend/main.py` — `gate_middleware` enforces on every route except open paths

**Open paths (must never be gated):**
- `/login` and `/login/` — the login page itself
- `/api/gate/*` — the gate API
- `/_next/*`, `/favicon*` — static assets (styling the login page requires these)

**Why:** M5's Basic Auth middleware only activated if both env vars were set; its
brute-force protection silently fail-open'd when Upstash Redis was unconfigured; it
offered no styled UI. The new gate has no external dependencies and fails closed.

**What breaks if violated:** Users see a plain browser popup again, or the gate can be
bypassed by omitting env vars, or static assets for the login page are blocked
(making the login screen unstyled and potentially broken).

---

## D-M7-2 — BigQuery write errors must always be captured and logged [M7]

**Rule:** All three write paths — `sync_to_bigquery()`, `sync_meta_to_bigquery()`, and
`sync_feedback_to_bigquery()` — must capture the return value of `insert_rows_json()`,
check it for errors, and log any rejections including how many of N rows were affected.

**Where it lives:** `backend/lib/bigquery.py` — all three sync functions.

**Why:** `insert_rows_json()` returns a list of error objects for rejected rows rather
than raising an exception. Without capturing this return value, rejected rows are
silently dropped and the function appears to succeed — the exact failure mode that
made the GSC warehouse appear empty across all milestones prior to M7.

**Testing mode:** Set `BQ_STRICT_WRITES=1` to raise on any rejected row instead of
logging. Use this during initial deployment to confirm rows actually land. Unset in
production so a warehouse hiccup never fails a user run.

**What breaks if violated:** GSC rows are rejected without any log message. The
warehouse appears to warm successfully but stays empty. Every subsequent run falls
through to the live GSC fetch path.

---

## D-M7-3 — Theme is driven by `[data-theme]` on `<html>`, not `prefers-color-scheme` [M7]

**Rule:** Dark/light mode is controlled by the `data-theme` attribute on the `<html>`
element. Do not use `@media (prefers-color-scheme: dark)` in `globals.css` as the
toggle mechanism.

**Where it lives:**
- `frontend/src/app/globals.css` — `:root, [data-theme="light"]` and `[data-theme="dark"]` blocks
- `frontend/src/app/layout.tsx` — inline `<script>` sets `data-theme` before paint (reads `localStorage`)
- `frontend/src/components/Brand.tsx` — `ThemeToggle` component writes `data-theme` and `localStorage`

**Why:** `prefers-color-scheme` reflects the OS setting and cannot be toggled by the
user within the app. The toggle requires a JS-driven attribute that persists across
page loads. An inline script in `layout.tsx` applies the saved theme before the browser
paints, preventing a flash of the wrong theme.

**What breaks if violated:** The theme toggle appears to work but resets to the OS
setting on every page load. Dark/light preference is not persisted.

# M8_CHANGES.md

## Alpha M8 / M8.1

Backend data correctness + ops-editable config + Step 1 selector chips.
Pipeline, scoring, and the locked output schema are unchanged.

---

### A. BigQuery snapshot correctness — `backend/lib/bigquery.py`

**Bug:** every sync writes a full 365-day GSC window stamped with that week's
Monday as the DATE partition value. The old read `SUM`med clicks and impressions
across *every* snapshot in range, so the second scheduled sync roughly doubled
metrics, the third tripled them, and so on. Manual one-off syncs never exposed
this; a weekly Cloud Scheduler job would have, starting the second Monday.

1. **Latest-snapshot read** (`fetch_from_bigquery`)
   - `QUALIFY date = MAX(date) OVER (PARTITION BY domain)` pins each domain to
     its own newest snapshot — per-domain, so one lagging domain neither
     disappears nor drags the others.
   - `MAX(clicks)` / `MAX(impressions)` replace `SUM`. Within a single snapshot
     duplicates are identical rows, so `MAX` is identity, not aggregation.
   - Impression-weighted position and the `GROUP BY account, domain, query, page`
     shape are unchanged. Row shape and output schema unchanged.
   - Freshness (D12) is judged on the **oldest** per-domain snapshot present, so
     a single stale domain marks the read stale rather than hiding behind a
     globally fresh max.

2. **Delete-partition-before-insert** (`sync_to_bigquery`)
   - This Monday's partition is deleted before the insert, making same-week
     re-runs, Scheduler retries, and double-triggers idempotent.
   - Runs only after a successful fetch returns rows — never delete-then-fail.
   - Best-effort: a delete failure logs and the insert proceeds; the read
     tolerates intra-snapshot duplicates via `MAX`.
   - **D11 preserved**: partition value stays a native `datetime.date`; the
     delete parameter is a typed `DATE`. No `str(monday)` anywhere in the path.

3. **Single-flight sync lock** — `backend/routers/admin.py`
   - `_auto_sync_running` guard; an overlapping trigger returns
     `{"status": "Skipped"}` with **HTTP 200**, not 409 — Cloud Scheduler treats
     non-2xx as failure and retries, which is the loop the guard prevents.
   - Released in a `finally` on every exit path (completion, early return,
     escaped exception) so a failure cannot wedge the flag.

---

### B. Step 1 selector chips — `frontend/src/app/page.tsx`, `globals.css`

Chips are **selection macros, not view filters**. Clicking "Nonprofit" selects
those domains, mirroring the existing "FFG only" button. Nothing is ever hidden:
the full flat grid stays rendered and every domain stays individually clickable.

- Chips hold **no state of their own** — they derive from and write to the
  parent's `selected` set, so chip state can never drift from grid state. This
  is the structural fix for the M7 accordion, which kept its own open/closed
  state and hid domains behind collapsed groups.
- Tri-state per chip: `none` → click selects the group; `partial` → click
  completes it; `all` → click clears it.
- Non-destructive: "add" never clears other verticals; "remove" only drops the
  URLs passed in.
- `aria-pressed` = `true` / `mixed` / `false`. State is also carried by a glyph
  (`✓` / `–` / `+`) and border style, so it survives color-blind viewing.
- The "All" chip was dropped — redundant with the existing Select all / Clear
  buttons.
- Grid order never changes on selection (position stability).

---

### C. Ops-editable config — `backend/data/*.json`, `backend/lib/config.py`

`CLIENT_VERTICAL_MAP` and `EXCLUDED_DOMAINS` moved out of `config.py` into JSON
so the partnerships/ops team can edit domain lists via a one-line Git diff
without touching Python.

- `backend/data/client_verticals.json` — one key per vertical, each an
  alphabetized array of bare hostnames. Alphabetical order keeps additions to a
  single line and makes merge conflicts unlikely.
- `backend/data/excluded_domains.json` — two keys, `inactive_clients` (Set A,
  16 from M5) and `generic_platforms` (Set B, 22 from M7). Both are unioned.
  The provenance is preserved as separate keys because Set B once *replaced*
  Set A instead of merging.
- **Fail closed.** A missing file, malformed JSON, a missing required key, or a
  hostname claimed by two verticals raises at import time. It never degrades to
  an empty mapping — an empty exclusion set silently restores all ~107 raw GSC
  properties to the picker.
- The loader normalizes to the same bare form `routers/domains.py::_to_host()`
  emits (lowercase, no scheme, no `www.`), so `www.jazzhr.com` and
  `jazzhr.com` collapse to one entry.
- Parity verified after extraction: 38 excluded domains, 83 vertical mappings
  (85 raw entries minus 2 `www.` duplicates the normalizer collapses).
- `config.py` retains engineer-owned constants only — weights, thresholds,
  category vocabularies.
- `backend/data/` ships in the image via the existing
  `COPY backend/ ./backend/` in the Dockerfile. No Dockerfile change needed.

---

### Explicitly unchanged (binding)

- Output schema: Page URL | Page Category | Topical Relevance Score | SEO Score
- Weights 70/30; composite computed before dedup (D2); no TF-IDF fallback (D1)
- D11: BigQuery DATE partition key is a native `datetime.date`
- D12: freshness gate short-circuits reads only, never the writeback
- D15: admin/cron sync uses `fetch_all_domains_live()`
- Catch-all static serving (no `app.mount`) so the gate middleware always fires
- Tier labels absent from the results table; `tier_label` retained in the
  export payload

---

### D. Regression fix — `backend/lib/tokenstore.py` (found via post-deploy testing)

**Bug:** `_set()` and `_del()` used `GET {url}/set/{key}/{value}` — the legacy
Upstash REST pattern with the value embedded in the URL path. OAuth token
JSON contains `{`, `"`, `:`, and `/`; URL-encoding that into a path segment
triggers a `301 Moved Permanently` from Upstash to a **re-encoded** location
(`%7B` becomes `%257B`), `httpx` follows the redirect automatically, and the
second request no longer matches the original command — the write silently
never happens. Symptom: `/setup` reports both accounts connected, but the
picker loads 0 domains, because `list_gsc_properties()` has no token to read.

**Fix:** `_set()` and `_del()` now POST to the bare Upstash REST URL with the
command as a JSON array (`["SET", key, value]` / `["SET", key, value, "EX", ex]`
/ `["DEL", key]`) — the documented Upstash pipeline-command form, which keeps
the value out of the URL entirely. `_get()` is unaffected (no value in the
path). Both now raise on an Upstash-reported `error` in the response body
instead of only checking the HTTP status.

This was flagged as fixed in a prior session (D-M4: "switched to POST with
JSON body") but the fix was not present in this codebase — same class of gap
as the missing `layout.tsx`: something that changed status without the
corresponding file landing in the milestone package. Both are now confirmed
present in this M8 package.

---

## M8.2 — warehouse persistence, copy cleanup, filter cleanup

### A. BigQuery now actually saves (`backend/lib/bigquery.py`)

Root causes of "GSC last sync: Never" with a sync that logged success:

1. **Nothing ever created the dataset or tables.** On a fresh project the first
   write failed with `NotFound`, the best-effort handler swallowed it, and the
   run reported success having written nothing. New `ensure_tables()` creates
   the dataset and all three tables with correct schemas and partitioning. It
   is idempotent, cached per process, and called at the start of every sync.
   It raises on failure: a sync that cannot guarantee its destination stops
   rather than reporting success.

2. **`bq.dataset(...).table(...)` is deprecated** in google-cloud-bigquery 3.x
   and raises on some versions. Replaced with explicit `TableReference`.

3. **The M8 delete-before-insert could not work.** Streamed rows sit in a
   write-optimized buffer for up to 90 minutes and cannot be deleted, so the
   DELETE failed exactly when a retry needed it. The GSC snapshot now writes via
   a **load job into the partition decorator `table$YYYYMMDD` with
   WRITE_TRUNCATE**: atomic partition replacement, idempotent by construction,
   no separate delete, and load jobs are free where streaming inserts are billed.

4. **Write verification.** After the load job, the code queries the partition
   and raises if it is empty. The log line reports the row count actually
   stored, not the job's self-reported status.

### ⚠️ This changes a binding rule — DECISIONS.md needs updating

**D11 must be scoped to streaming inserts only.** Load jobs serialize the
payload as JSON, so the DATE column takes an **ISO string**; passing a
`datetime.date` raises `Object of type date is not JSON serializable`. The GSC
snapshot path now uses `monday.isoformat()` and that is correct. The old rule
("never `str(monday)`") still holds for `insert_rows_json`, which now only the
single-row feedback write uses. Please review and amend D11 before the next
session so this is not "fixed" back.

### B. Diagnostics

- `GET /api/admin/status` now returns `gsc_rows`, `meta_rows`,
  `gsc_table_exists`, `meta_table_exists`, and a `detail` string.
  "Never" now distinguishes *table missing* (credentials or IAM) from *table
  empty* (the GSC fetch returned nothing).
- New `GET /api/admin/bq-check`: one request that walks env vars → credentials →
  client → dataset/tables → writes and reads back a probe row. The probe uses
  the `1970-01-01` partition with WRITE_TRUNCATE so it never touches a real
  snapshot and cannot accumulate.
- Admin card now shows row counts next to each date.

### C. Copy cleanup

Em dashes and en dashes removed from all user-facing strings in
`page.tsx`, `admin/page.tsx`, and backend `log()` lines in `admin.py`,
`gsc.py`, `relevance.py`. Decorative `▸` / `!` / `✓` prefixes dropped from log
lines. Phrasing rewritten to plain sentences: "nothing is hidden" and similar
self-explaining clauses removed. Score tooltips read "0 to 10" instead of
"0–10". The partial-chip glyph is now `~` instead of an en dash.
Code comments and DECISIONS.md were left alone as engineer-facing.

### D. "Other" removed from the filter chips

`VERTICAL_ORDER` no longer includes "Other". An unmapped domain should be
classified in `client_verticals.json` or excluded in `excluded_domains.json`,
not given a catch-all bulk-select button. Unmapped domains still appear in the
grid and stay individually selectable, so nothing became unreachable.

**Follow-up before this is really done:** list which domains currently have no
vertical and either map or exclude them. Removing the chip removes the symptom;
the unclassified domains are the actual finding.

### E. Not included, deliberately

**Incremental sync** (fetch only days since the last sync instead of a full
365-day window) is the single largest speed lever, but it conflicts with the
weekly-snapshot model the latest-snapshot read depends on. It changes the
schema contract and needs its own spec, not a retrofit. Per-domain progress
already appears in the sync log, so a hung run is now distinguishable from a
slow one.

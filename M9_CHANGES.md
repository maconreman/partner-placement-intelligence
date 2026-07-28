# M9_CHANGES.md

## Alpha M9 — ingestion plane moved off the app (free)

The pipeline, scoring, dedup, and locked output schema are unchanged. M9 changes
only where and how the scheduled sync runs.

---

### A. New standalone entrypoint — `jobs/sync.py`, `jobs/__init__.py`

- Runs the same Phase 1 (live GSC fetch) and Phase 2 (metadata crawl) logic that
  `routers/admin.py::auto_sync` ran as a fire-and-forget background task, lifted
  into a script: `python -m jobs.sync`.
- Reuses `backend.lib.*` verbatim — `fetch_all_domains_live`,
  `list_gsc_properties`, `fetch_from_bigquery`, `aggregate_to_pages`, the three
  classify passes, and `crawl_metadata_for_pages`. No logic is reimplemented.
- Relies on the internal warehouse write inside `_live_gsc_fetch` rather than a
  second `sync_to_bigquery` call. Under M8's WRITE_TRUNCATE load job the write is
  idempotent, so there is one write, not two.
- Loads a local `.env` if `python-dotenv` is present, before importing any
  backend module, so a developer can run it locally. On a runner the env comes
  from Actions secrets and the `.env` load is a silent no-op.
- `--phase all|gsc|metadata` selects a phase; default is both.
- Exits non-zero on any failure so a CI runner marks the job red. `SystemExit`
  codes: 2 = BigQuery not configured, 3 = fetch returned no rows, 1 = unhandled.

### B. Two workflows — `.github/workflows/`

- `warehouse-sync.yml` — Mondays 04:00 UTC, plus manual `workflow_dispatch` with
  a phase picker. Sets `BQ_STRICT_WRITES=true`. A `concurrency` group serializes
  runs so a manual trigger during a scheduled run waits instead of doubling GSC
  quota — the workflow-level analog of M8's single-flight guard.
- `keepalive.yml` — every 12 hours, pings `/api/gate/status` to reduce Space
  cold starts. Replaces UptimeRobot. Tolerates non-2xx: the goal is to wake the
  Space, not to assert health.

### C. What is retired

- `AUTO_SYNC_SECRET` and `/api/admin/auto-sync/{secret}` as the scheduled
  trigger. Nothing should be pointed at that endpoint anymore. Deleting the
  endpoint and the env var from the app is a follow-up (see handoff, Cutover).
- UptimeRobot, replaced by `keepalive.yml`.

### D. Explicitly unchanged (binding)

- Output schema: Page URL | Page Category | Topical Relevance Score | SEO Score.
- Weights 70/30; composite before dedup (D2); no TF-IDF fallback (D1).
- The weekly-snapshot write model and the M8 load-job partition write (D-M8-1).
- The manual admin sync buttons (`/api/admin/sync-gsc`, `/api/admin/sync-metadata`),
  kept as a gated fallback (D-M9-3).
- The full 365-day fetch window. M9 does not make the fetch smaller; it makes it
  free by relocating it. Incremental delta fetch is M9.1 (D-M9-5, deferred).

### E. Not included, deliberately

- **Incremental delta fetch** (7 days instead of 365). This is the real speed
  lever, and it is a schema-contract change: the GSC request would gain a `date`
  dimension, `GscRow` would carry a date, and aggregation, pipeline, and rank
  would change to match. It supersedes the weekly-snapshot semantics D-M8-1
  depends on and needs its own spec. See D-M9-5.
- **Watermark table.** It is a prerequisite for incremental fetch, so it lands
  with M9.1, not before.

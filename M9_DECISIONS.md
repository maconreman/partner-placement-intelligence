# Nexus Placement Intelligence — M9 Architectural Decisions

Companion to `DECISIONS.md` and `M8_DECISIONS.md`. Binding for M9 and later.

M9 is a relocation milestone. It moves *where* ingestion runs, not *what* it
computes. No scoring, schema, dedup, or output rule changes. Every M8 decision
still holds.

---

## D-M9-1 — Ingestion runs on a scheduled runner, not inside the app

**Decision.** The weekly GSC sync and metadata crawl run in a GitHub Actions
workflow (`.github/workflows/warehouse-sync.yml`) that executes
`python -m jobs.sync` on a GitHub-hosted runner. The app no longer triggers or
hosts a scheduled sync.

**Why.** The in-app ingestion path had three structural faults that concurrency
tuning cannot fix: the Space sleeps after about 48 hours idle, a sync inherited
the lifetime of whatever request launched it, and it was reachable through an
unauthenticated secret-in-URL endpoint on a public app. A runner has none of
these: it wakes on a cron, has its own 6-hour ceiling per job (we cap at 120
minutes), and takes its secrets from the Actions secret store.

**Feasibility.** The runner can do everything the app can because GSC OAuth
tokens live in Upstash and BigQuery credentials come from an env var. `jobs/sync`
imports the same `backend.lib.*` modules the app uses. There are two entrypoints
into one codebase, not two codebases.

**Do not.** Reintroduce a scheduled trigger that depends on the Space being
awake, or that runs the full pipeline inside an HTTP request.

---

## D-M9-2 — `AUTO_SYNC_SECRET` and the `/api/admin/auto-sync/{secret}` endpoint are retired [SUPERSEDES the M7 cron endpoint]

**Decision.** The runner is the only scheduled sync trigger. The secret-in-URL
cron endpoint is no longer the mechanism of record and should be removed from the
app in the follow-up that deletes it (see "Cutover" in the handoff). Until it is
deleted, it must not be scheduled against.

**Why.** A secret in a URL path on a public deployment is logged by proxies,
retained in browser and CDN history, and cannot be rotated without a redeploy.
The runner authenticates to Upstash and GCP with rotatable, store-held secrets
that never appear in a URL.

**Do not.** Point any scheduler, uptime monitor, or bot at
`/api/admin/auto-sync/...`. Do not add a new secret-bearing GET endpoint to
replace it.

---

## D-M9-3 — The manual admin sync buttons remain, unchanged, as a fallback

**Decision.** `/api/admin/sync-gsc` and `/api/admin/sync-metadata` (the streaming
admin-card buttons) stay exactly as they are. They are the human-in-the-loop
fallback for a one-off resync and for watching progress live.

**Why.** They are gated (session cookie required), they stream progress the
runner log cannot show in real time to a non-engineer, and they are useful when
someone wants to force a refresh without waiting for Monday. They are not a
security exposure the way the unauthenticated cron endpoint was.

**Caveat.** These run on the Space and still meet the request-lifetime and sleep
constraints. For a full 100-plus-domain sync, prefer the runner. The buttons are
for targeted or observational use.

---

## D-M9-4 — CI runs are strict; a silent warehouse write failure fails the job

**Decision.** The workflow sets `BQ_STRICT_WRITES=true`. On the runner, a
BigQuery write that reports success while writing nothing raises and turns the
job red, rather than being logged and swallowed.

**Why.** The entire M8 warehouse-persistence saga was a write that reported
success and stored nothing. In production the app keeps `BQ_STRICT_WRITES` unset
so a warehouse hiccup never fails a user's run — that trade-off is correct for a
user request. It is the wrong trade-off for an unattended sync, where a silent
failure means a week of stale data with a green checkmark.

**Do not.** Unset `BQ_STRICT_WRITES` in the workflow to make a flaky run pass.

---

## D-M9-5 — Keep-alive is a workflow, and it is a mitigation with an expiry

**Decision.** `.github/workflows/keepalive.yml` pings `/api/gate/status` every 12
hours to reduce cold starts. It replaces UptimeRobot.

**Why.** One system, versioned and visible, instead of an external account with
its own credentials and dashboard. But it is a mitigation, not a fix: it reduces
the frequency of cold starts, it does not remove them.

**Expiry.** When a warm instance lands (Cloud Run `min-instances=1`, deferred on
budget), delete this workflow. A keep-alive ping against a warm instance is
pointless.

---

## Deferred to M9.1, with a hard boundary

**Incremental (delta) GSC fetch.** Fetching only the days since the last
successful sync — roughly 7 days instead of 365 — is the largest remaining speed
lever and the reason the free proposal quoted a large speedup. It is **not in
M9** and must not be bolted on.

The blocker is concrete: `_live_gsc_fetch` requests only
`dimensions: ["query", "page"]` and aggregates the whole window into one row per
page. `GscRow` carries no per-day date. True daily-incremental facts require
adding the `date` dimension to the GSC request, extending `GscRow`, and updating
aggregation, the pipeline, and rank to carry it. That is a schema-contract change
across several files and it supersedes the weekly-snapshot semantics that
D-M8-1's load-job partition write and the latest-snapshot read depend on.

M9.1 gets its own spec: append-only daily-partitioned facts, a per-domain
watermark table, a rolling-window read that replaces the `QUALIFY MAX(date)`
snapshot read, and the migration path from weekly snapshots to daily facts.
Until that spec exists, the runner fetches the full 365-day window, exactly as
the app did. M9 makes that fetch cost nothing extra by moving it off the app and
onto free infrastructure; it does not make the fetch smaller.

**Cloud Run migration.** Unchanged from M8: spec complete, blocked on budget.
The `jobs/sync` entrypoint written for the runner is the same entrypoint a Cloud
Run Job would invoke, so that migration becomes a scheduler swap rather than a
rewrite.

# Nexus Placement Intelligence — M9 Handoff and Setup Guide

**Milestone:** Alpha M9 (ingestion plane moved off the app, free)
**Starts from:** M8 (FastAPI + Next.js, Docker on Hugging Face Spaces)
**Status:** built and validated locally; not yet run on a real runner
**Read alongside:** `M9_DECISIONS.md`, `M9_CHANGES.md`, `M8_DECISIONS.md`

---

## 1. What M9 does in one paragraph

The scheduled warehouse sync no longer runs inside the app. It runs in a GitHub
Actions runner that executes `python -m jobs.sync`, using the same code the app
uses and the same secrets. This removes the three structural faults of the
in-app sync at once: the Space sleeping mid-sync, the sync inheriting a request
lifetime, and an unauthenticated secret-in-URL cron endpoint on a public app.
Cost is zero: the repo is public, so Actions minutes are unlimited, and the sync
already writes to BigQuery via free load jobs. What M9 does not do is make the
GSC fetch smaller — that is M9.1, and it needs its own schema spec.

---

## 2. Files in this drop

```
jobs/
  __init__.py
  sync.py                       standalone entrypoint: python -m jobs.sync
.github/workflows/
  warehouse-sync.yml            weekly sync + manual dispatch
  keepalive.yml                 12-hourly ping, replaces UptimeRobot
M9_DECISIONS.md
M9_CHANGES.md
M9_HANDOFF.md                   this file
```

Nothing under `backend/` or `frontend/` changes in M9. The entrypoint imports
existing modules; it does not modify them.

---

## 3. Setup guide (about 15 minutes)

### Step 1 — Add the files to the repo

Copy `jobs/` and `.github/workflows/` into the repo root and commit to `main`.
Because `.github/workflows/` is owned by the repo and is not part of the app
image, this does not affect the Space build.

### Step 2 — Add the Actions secrets

In the repo: **Settings, then Secrets and variables, then Actions, then New
repository secret**. Add each of these with the same values the Space uses
(Space: **Settings, then Variables and secrets**):

| Secret | What it is |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth web client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth web client secret |
| `APP_BASE_URL` | The production Space URL, e.g. `https://your-space.hf.space` |
| `UPSTASH_REDIS_REST_URL` | Upstash REST URL (holds the GSC tokens) |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash REST token |
| `BQ_PROJECT_ID` | BigQuery project |
| `BQ_DATASET_ID` | BigQuery dataset |
| `GCP_SERVICE_ACCOUNT_JSON` | The service account JSON, pasted whole |
| `HF_API_TOKEN` | HF Inference token |

Optional tuning as a **variable** (not a secret): `GSC_MAX_WORKERS` (default 16).

Confirm the service account has **BigQuery Data Editor** and **BigQuery Job
User** on the project, and that both Google accounts are already connected at
`/setup` (the runner reads their tokens from Upstash; it cannot connect them).

### Step 3 — Do a dry run by hand before trusting the schedule

Actions tab, pick **warehouse-sync**, **Run workflow**, choose phase **gsc**.
Watch the log. You want to see a non-zero row count on the line
`GSC sync complete: N rows fetched and persisted.` If it fails:

- `BigQuery not configured` (exit 2): a `BQ_*` or `GCP_SERVICE_ACCOUNT_JSON`
  secret is missing.
- `No GSC data returned` (exit 3): the accounts are not connected at `/setup`, or
  the service account lacks Data Editor.
- A parse error on `GCP_SERVICE_ACCOUNT_JSON`: the JSON was pasted with extra
  quotes or truncated.

Then run it again with phase **metadata**, then once with phase **all**.

### Step 4 — Prove idempotency (the M8 check, now on the runner)

Run **warehouse-sync** (phase gsc) twice in the same week. Open the admin card
or `/api/admin/status`. The row count and click totals must stay flat, not
double. If they grow, stop and check that `sync_to_bigquery` is still doing a
WRITE_TRUNCATE load into the partition decorator (M8 D-M8-1). Do not leave the
schedule enabled if numbers grow.

### Step 5 — Let the schedule run

Once Steps 3 and 4 pass, the schedules are already live (they are defined in the
workflow files). The next Monday 04:00 UTC sync runs on its own, and the
keep-alive pings every 12 hours starting immediately.

### Step 6 — Cutover: retire the old cron

Once a scheduled run has succeeded on its own:

- Turn off UptimeRobot (or whatever was pinging the Space).
- Remove any external scheduler that hits `/api/admin/auto-sync/...`.
- Plan the follow-up commit that deletes the `auto-sync` endpoint and the
  `AUTO_SYNC_SECRET` env var from the app (D-M9-2). Not required for M9 to work,
  but the endpoint is a standing exposure until it is gone.

---

## 4. Running it locally (optional)

```
pip install -r backend/requirements.txt
# put the same secrets in a local .env (python-dotenv picks it up)
python -m jobs.sync --phase gsc
```

The `.env` is only for local runs. It must never be committed — the repo is
public and `.gitignore` already excludes it.

---

## 5. Verification checklist

- [ ] `warehouse-sync` phase gsc logs a non-zero persisted row count.
- [ ] `warehouse-sync` phase metadata completes without error.
- [ ] Two gsc runs in one week leave row and click totals flat, not doubled.
- [ ] The admin card / `/api/admin/status` shows a recent GSC sync date.
- [ ] A user run reads from the warehouse (logs it is fresh) rather than fetching
      live.
- [ ] `keepalive` runs green and the Space answers `/api/gate/status`.
- [ ] UptimeRobot and the `auto-sync` cron are retired (Step 6).

---

## 6. Known limits and what comes next

- **Cron drift.** Scheduled workflows can start several minutes late under load.
  Irrelevant for a weekly batch.
- **Cold start still exists.** Keep-alive reduces it; only a warm instance
  removes it. Deferred on budget (Cloud Run `min-instances=1`).
- **The fetch is still 365 days.** M9 relocated it; it did not shrink it.
  Incremental delta fetch is M9.1 and is the next real speed work. It needs the
  schema spec described in D-M9-5 before any code: append-only daily facts, a
  per-domain watermark table, and a rolling-window read to replace the
  latest-snapshot read.
- **Migration path stays clean.** `jobs/sync` is the same entrypoint a Cloud Run
  Job would invoke, so the eventual paid migration is a scheduler swap, not a
  rewrite.

---

## 7. Working conventions (unchanged)

- Planning before implementation; explicit approval first.
- Whole-file replacements, not diffs.
- `flat.txt` is the canonical per-milestone source artifact, milestone-prefixed.
- Documentation set per milestone: handoff, decisions, changes, flat, ZIP.
- US standard English spelling.

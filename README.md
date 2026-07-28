---
title: Nexus Placement Intelligence
emoji: 🎯
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# Nexus Placement Intelligence — Alpha M9

Single-deployment FastAPI + Next.js app that scores content-placement opportunities
across the FFG network. **M9** migrates the production stack to Hugging Face Spaces
(Docker), replaces the old Basic Auth popup with a styled username/password gate,
hardens the BigQuery write path so GSC data actually persists, and ships a UI
refresh with a dark/light toggle. It builds on M4's single-app consolidation and
cold-run speed work. The partnerships-team experience is unchanged in shape.

```
nexus-placement-intelligence/
├── backend/            FastAPI app (API + pipeline + BigQuery warehouse)
│   ├── main.py         entry point (load_dotenv first — D14) + access-gate middleware
│   ├── lib/            pipeline, gsc, crawl, classify, rank, tokenstore, gate, …
│   └── routers/        gate, auth, domains, run, export, feedback, admin
├── frontend/           Next.js static export
│   └── src/app/        page.tsx (tool) · login/ · setup/ · admin/
├── Dockerfile          builds frontend → serves everything from FastAPI :7860
├── DECISIONS.md        binding architectural decisions — read first
└── .env.example
```

See `M9_CHANGES.md` for exactly what changed and why, and `DECISIONS.md` for the
binding rule set.

---

## Prerequisites

- **Python 3.11** (matches the Docker image; 3.11/3.12 both work locally)
- **Node.js 20** (for the Next.js build)
- An **Upstash Redis** REST DB, a **Google OAuth 2.0 *Web application*** client, an
  **HF Inference** token, and optionally a **BigQuery** dataset + service account.
  All are configured through `.env` (see below). With `BQ_PROJECT_ID` left unset the
  tool runs *live-only* (fetch + crawl every run) and still works end to end.

---

## 1. Configure environment

```bash
cp .env.example .env
```

Fill in `.env`. At minimum, generate a session secret (now **required** — M4 item 5):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

…and paste it as `SESSION_SECRET`. The OAuth redirect URI to register in GCP is
`{APP_BASE_URL}/api/auth/callback` (e.g. `http://localhost:7860/api/auth/callback`).

---

## 2. Run locally (the way it runs in production)

The FastAPI server serves the API **and** the built frontend from port 7860, so the
simplest path is: build the frontend once, then run uvicorn.

```bash
# ── Frontend: build the static export into frontend/out ──
cd frontend
npm install
npm run build          # emits frontend/out/  (incl. /admin and /setup routes)
cd ..

# ── Backend: install deps and run ──
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 7860
```

Open **http://localhost:7860** — the partnerships tool. The admin dashboard is at
**http://localhost:7860/admin**, account setup at **/setup**.

> Re-run `npm run build` whenever you change the frontend. The backend picks up the
> refreshed `frontend/out` automatically (no uvicorn restart needed for static files).

### Iterating on the frontend only

For fast UI iteration you can run the Next.js dev server on :3000, but note that
`next.config.mjs` uses `output: "export"` (static) with no API proxy, so API calls
expect the FastAPI origin. The build-then-serve flow above is the reliable local loop.

---

## 3. Run with Docker (single container, mirrors HF Spaces)

```bash
docker build -t nexus-placement-intelligence .
docker run --rm -p 7860:7860 --env-file .env nexus-placement-intelligence
# → http://localhost:7860
```

The Dockerfile builds the Next.js export in one stage and serves it from the Python
stage, exactly as the Hugging Face Space does. One Space, one Dockerfile, one set of
secrets, one OAuth redirect URI.

---

## 4. Populating the warehouse (admin)

Once both Google accounts are connected at `/setup`, open `/admin` and run:

1. **Sync GSC data** — live fetch for all domains → BigQuery (bypasses the freshness
   gate; D-S3 / D15).
2. **Sync page metadata** — crawls pages from the warehouse → BigQuery.

User-facing runs (`/api/run`) read from BigQuery first and only fetch live when the
warehouse is stale or empty (D12). The M4 fetch/crawl overlap (item 2) activates only
on those cold runs.

---

## Tests

The repo ships no test runner, but the M4 changes were verified with focused checks:
HMAC sign/verify (valid, tampered, malformed, missing-secret), the in-memory metadata
cache + shared-client crawl path, the URL-only pre-warm filter, and an integrated
`run_pipeline` overlap test confirming each page is crawled exactly once (pre-warm
warms the cache; the authoritative crawl hits it) with identical merged metadata.

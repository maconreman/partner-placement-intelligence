# FFG Universe — Placement Intelligence (Next.js port)

Alpha · M3.4 — internal SEO placement-intelligence tool. Queries Google Search
Console across the FFG network + client domains, scores pages for topical
relevance and SEO strength, and exports ranked placement lists.

This build carries the **M3.4 design pass** (OS-native dark/light, Colab Cell 5
layout fidelity, sharp data-tool aesthetic). Pipeline logic, routing, API calls,
and auth flow are unchanged from the prior port.

---

## 1 · Local testing (VS Code)

```bash
npm install
cp .env.example .env.local      # then fill in the values below
npm run dev                     # http://localhost:3000
```

Required `.env.local` values:

| Var | Where it comes from |
| --- | --- |
| `APP_BASE_URL` | `http://localhost:3000` locally; your Vercel alias in prod |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | GCP OAuth client — **type "Web application"** |
| `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis → REST API section |
| `HF_API_TOKEN` | HuggingFace inference token (BAAI/bge-base-en-v1.5) |

> **Storage / caching.** By default the tool caches GSC traffic (7-day) and
> page metadata (90-day) in Google Sheets — no extra setup. To switch the
> primary read path to a BigQuery warehouse (sub-second multi-domain reads,
> recommended at Beta scale), follow **BIGQUERY_SETUP.md**. BigQuery activates
> automatically when its env vars are present; Sheets remains the fallback.

In GCP, add the redirect URI `<APP_BASE_URL>/api/auth/callback` to the OAuth
client. Visit `/setup` to connect both `data@` and `analytics@` accounts.

Useful checks:

```bash
npm run typecheck     # tsc --noEmit
npm run lint          # next lint
npm run build         # production build
```

## 2 · Push to GitHub

```bash
git init
git add .
git commit -m "FFG Universe — M3.4 design pass"
git branch -M main
git remote add origin git@github.com:<org>/ffg-universe-vercel.git
git push -u origin main
```

`.env*` files are git-ignored — secrets never get committed.

## 3 · Deploy to Vercel

1. Import the GitHub repo at vercel.com → **New Project**.
2. **Plan must be Pro** — `/api/run` needs `maxDuration=300s`; the Hobby plan
   caps functions at 60s and universal first-run crawls exceed that.
3. Add every `.env.local` var under **Settings → Environment Variables**,
   setting `APP_BASE_URL` to your stable Vercel alias.
4. In GCP, add `<vercel-alias>/api/auth/callback` as a second redirect URI.
5. Deploy, then open `/setup` on the live URL to connect both accounts.

`vercel.json` pins the function timeouts (`run=300s`, `export=120s`,
`domains=60s`).

---

## Dark / light mode

Fully OS-native via `prefers-color-scheme` — no toggle, no JavaScript. Switch
your OS appearance to preview both. Every component reads only semantic CSS
variables, so the swap is automatic. The console/terminal block stays
intentionally dark in both modes.

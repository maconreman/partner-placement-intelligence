"""
main.py — FastAPI entry point for the combined nexus-placement-intelligence app.

M4 item 1: the partnerships tool and the admin dashboard are now served from a
single deployment. All routers — including admin — are mounted here, and the
Next.js static export (with its /admin route) is served at root.

D14: load_dotenv() runs first, before any local import, so that config.py reads
the correct values when it evaluates USE_BIGQUERY / BQ_PROJECT_ID etc. at module
import time. Without this, .env is never loaded and every _require_env() call
raises RuntimeError even when .env is present (the confirmed M3.5 boot bug, and
the upstream cause of the admin "0 domains" symptom via D-S5).
"""
from dotenv import load_dotenv
load_dotenv()  # must precede all local imports (config.py reads env at import time)

from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .routers.auth import router as auth_router
from .routers.gate import router as gate_router  # M7: app access gate
from .routers.domains import router as domains_router
from .routers.run import router as run_router
from .routers.export import router as export_router
from .routers.feedback import router as feedback_router
from .routers.admin import router as admin_router  # M4 item 1: merged from admin app
from .lib.gate import GATE_COOKIE, cookie_valid
from .lib.config import GATE_ENABLED

app = FastAPI(title="Nexus Placement Intelligence", docs_url=None, redoc_url=None)

# ── Access gate (M7) ──────────────────────────────────────────────────────────
# Every request must carry a valid gate cookie EXCEPT the paths listed here.
# When the gate is disabled (credentials unset) this middleware is a no-op.
#
# Open path rules:
#   /api/gate      — gate login/logout/status (reachable before sign-in by design)
#   /login         — the gate login page itself
#   /_next         — Next.js compiled assets (needed to render the login page)
#   /favicon       — favicon (no auth needed)
#   /404           — 404 page
#   /api/admin/auto-sync
#                  — unauthenticated cron endpoint; secured by secret_key path
#                    parameter instead of session cookie (see admin.py)
GATE_OPEN_PREFIXES = (
    "/api/gate",
    "/login",
    "/_next",
    "/favicon",
    "/404",
    "/api/admin/auto-sync",
)


def _is_open_path(path: str) -> bool:
    return any(path == p or path.startswith(p) for p in GATE_OPEN_PREFIXES)


@app.middleware("http")
async def gate_middleware(request: Request, call_next):
    if not GATE_ENABLED or _is_open_path(request.url.path):
        return await call_next(request)
    if cookie_valid(request.cookies.get(GATE_COOKIE)):
        return await call_next(request)
    # API calls get a 401 (the client surfaces it); page loads redirect to login.
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Sign in required"}, status_code=401)
    return RedirectResponse(url="/login/", status_code=302)


# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(gate_router)   # M7: /api/gate/* — must be reachable pre-auth
app.include_router(auth_router)
app.include_router(domains_router)
app.include_router(run_router)
app.include_router(export_router)
app.include_router(feedback_router)
app.include_router(admin_router)  # /api/admin/* — D-S3 admin-side function-call boundary

# ── Static Next.js export ─────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "out"

if STATIC_DIR.exists():
    # Serve the Next.js static export through the catch-all route so every
    # request — including root / — passes through gate_middleware first.
    #
    # IMPORTANT: do NOT use app.mount("/", StaticFiles(...)) here. Starlette
    # mounted sub-applications bypass HTTP middleware entirely, so a StaticFiles
    # mount at "/" would serve index.html to unauthenticated visitors without the
    # gate ever firing. The catch-all below goes through middleware correctly.
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Try exact file first (covers _next/static/*, favicon.ico, etc.)
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        # Try appending /index.html (Next.js trailingSlash: true output)
        index = candidate / "index.html"
        if index.is_file():
            return FileResponse(index)
        # Try full_path.html
        html = STATIC_DIR / f"{full_path}.html"
        if html.is_file():
            return FileResponse(html)
        # Fallback to root index (SPA catch-all)
        root_index = STATIC_DIR / "index.html"
        if root_index.is_file():
            return FileResponse(root_index)
        not_found = STATIC_DIR / "404.html"
        return FileResponse(not_found if not_found.exists() else root_index)

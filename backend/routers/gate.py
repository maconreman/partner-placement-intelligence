"""
routers/gate.py — Application access gate endpoints (M7).

  POST /api/gate/login   { username, password } → sets signed cookie on success
  POST /api/gate/logout  → clears the cookie
  GET  /api/gate/status  → { enabled, authed }

This is the access layer that decides WHO can open the app. It is separate from
/api/auth/* (Google OAuth), which authorizes the GSC data accounts the tool
reads from.
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from ..lib.gate import (
    GATE_COOKIE, credentials_match, issue_cookie, cookie_valid,
)
from ..lib.config import GATE_ENABLED, GATE_SESSION_MAX_AGE

router = APIRouter(prefix="/api/gate")


@router.post("/login")
async def gate_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    if not credentials_match(username, password):
        return JSONResponse({"ok": False, "error": "Incorrect username or password"}, status_code=401)

    resp = JSONResponse({"ok": True})
    # Secure flag is set by the proxy/HF Spaces (HTTPS); SameSite=Lax is safe for
    # a same-origin login. HttpOnly keeps the token out of JavaScript.
    resp.set_cookie(
        GATE_COOKIE, issue_cookie(),
        max_age=GATE_SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return resp


@router.post("/logout")
async def gate_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(GATE_COOKIE, path="/")
    return resp


@router.get("/status")
async def gate_status(request: Request):
    authed = cookie_valid(request.cookies.get(GATE_COOKIE))
    return {"enabled": GATE_ENABLED, "authed": authed}

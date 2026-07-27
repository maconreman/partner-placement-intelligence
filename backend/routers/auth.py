"""
routers/auth.py — Google OAuth2 flow.
/api/auth/start    → consent URL redirect
/api/auth/callback → exchange code → Upstash → session cookie → /setup?connected=...
/api/auth/status   → { data, analytics, ready }
/api/auth/signout  → clear session
"""
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from ..lib.tokenstore import get_auth_status, create_session, delete_session, get_session
from ..lib.googleauth import build_auth_url, exchange_code, app_base_url

router = APIRouter(prefix="/api/auth")

SESSION_COOKIE = "ffg_session"
SESSION_MAX_AGE = 7 * 24 * 3600


@router.get("/start")
async def auth_start(account: str):
    if account not in ("data", "analytics"):
        return Response(content='{"error":"account must be data or analytics"}', status_code=400, media_type="application/json")
    url = build_auth_url(account)  # type: ignore
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def auth_callback(request: Request):
    params = dict(request.query_params)
    code = params.get("code")
    account = params.get("state")
    error = params.get("error")
    base = app_base_url()
    # Bug fix: secure cookie only on HTTPS — localhost is plain HTTP
    is_https = base.startswith("https://")

    def back(qs: str) -> RedirectResponse:
        return RedirectResponse(url=f"{base}/setup?{qs}", status_code=302)

    if error:
        return back(f"error={error}")
    if not code:
        return back("error=missing_code")
    if account not in ("data", "analytics"):
        return back("error=bad_state")

    try:
        await exchange_code(code, account)  # type: ignore
    except ValueError as e:
        return back(f"error={e}&account={account}")
    except Exception as e:
        return back(f"error={str(e)[:120]}")

    # M4 item 5: create_session returns the HMAC-signed cookie value.
    cookie_value = await create_session({"account": account})
    resp = RedirectResponse(url=f"{base}/setup?connected={account}", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE, cookie_value,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=is_https,   # False on localhost, True on HF Spaces
        samesite="lax",
    )
    return resp


@router.get("/status")
async def auth_status():
    try:
        status = await get_auth_status()
        return {**status, "ready": status["data"] and status["analytics"]}
    except Exception as e:
        return {"data": False, "analytics": False, "ready": False, "error": str(e)}


@router.post("/signout")
async def auth_signout(request: Request):
    cookie_value = request.cookies.get(SESSION_COOKIE)
    if cookie_value:
        await delete_session(cookie_value)  # verifies HMAC, then deletes by bare sid
    resp = Response(content='{"ok":true}', media_type="application/json")
    resp.delete_cookie(SESSION_COOKIE)
    return resp

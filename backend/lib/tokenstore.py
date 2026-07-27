"""
tokenstore.py — Upstash Redis store for OAuth tokens and session cookies.
Ports tokenStore.ts plus adds M3.5 session management (replaces Basic Auth).

Two key namespaces:
  ffg:token:{account}   — OAuth refresh + access tokens for data@ / analytics@
  ffg:session:{sid}     — Session → account mapping (7-day TTL)
"""
from __future__ import annotations
import os
import json
import hmac
import hashlib
import secrets
import time
import httpx
from typing import Optional, Literal

AccountKey = Literal["data", "analytics"]

TOKEN_PREFIX = "ffg:token:"
SESSION_PREFIX = "ffg:session:"
SESSION_TTL = 7 * 24 * 3600  # 7 days in seconds


# ── M4 item 5: HMAC session signing ───────────────────────────────────────────
# The cookie value is `{sid}.{hmac_sha256(sid, SESSION_SECRET)}`. The signature
# is verified on every read BEFORE Upstash is touched, so unsigned or tampered
# cookies are rejected without a lookup. SESSION_SECRET was documented in
# .env.example since M3.5 but was previously inert; it is now required and live.

def _session_secret() -> str:
    sec = os.environ.get("SESSION_SECRET", "")
    if not sec:
        raise RuntimeError(
            "SESSION_SECRET is not set. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return sec


def _sign_sid(sid: str) -> str:
    sig = hmac.new(_session_secret().encode(), sid.encode(), hashlib.sha256).hexdigest()
    return f"{sid}.{sig}"


def _unsign_value(value: str) -> Optional[str]:
    """Verify a signed cookie value and return the bare session ID, or None."""
    if not value or "." not in value:
        return None
    sid, _, sig = value.partition(".")
    if not sid or not sig:
        return None
    expected = hmac.new(_session_secret().encode(), sid.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return sid


def _redis_url() -> str:
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "")
    if not url:
        raise RuntimeError("UPSTASH_REDIS_REST_URL is not set.")
    return url.rstrip("/")


def _redis_token() -> str:
    tok = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
    if not tok:
        raise RuntimeError("UPSTASH_REDIS_REST_TOKEN is not set.")
    return tok


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_redis_token()}"}


async def _get(key: str) -> Optional[str]:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_redis_url()}/get/{key}", headers=_headers())
        r.raise_for_status()
        body = r.json()
        val = body.get("result")
        return val  # None if key missing


async def _set(key: str, value: str, ex: Optional[int] = None) -> None:
    """
    POST the SET command as a JSON array to the Upstash pipeline endpoint.

    The original GET /set/{key}/{value} path-encoding approach caused silent
    failures for OAuth tokens: base64 values contain '/', '+', and '=' which
    get URL-mangled or trigger 301 redirects from Upstash's REST API. Posting
    the command as a JSON body avoids all encoding issues.
    """
    async with httpx.AsyncClient() as client:
        command = ["SET", key, value]
        if ex:
            command.extend(["EX", str(ex)])
        r = await client.post(f"{_redis_url()}/", headers=_headers(), json=command)
        r.raise_for_status()


async def _del(key: str) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_redis_url()}/del/{key}", headers=_headers())
        r.raise_for_status()


# ── OAuth token storage ───────────────────────────────────────────────────────

async def get_token(account: AccountKey) -> Optional[dict]:
    """Return stored OAuth token dict for account, or None."""
    raw = await _get(f"{TOKEN_PREFIX}{account}")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def set_token(account: AccountKey, token: dict) -> None:
    """Persist OAuth token dict for account."""
    await _set(f"{TOKEN_PREFIX}{account}", json.dumps(token))


async def delete_token(account: AccountKey) -> None:
    await _del(f"{TOKEN_PREFIX}{account}")


async def get_auth_status() -> dict[str, bool]:
    data_tok = await get_token("data")
    analytics_tok = await get_token("analytics")
    data_ok = bool(data_tok and data_tok.get("refresh_token"))
    analytics_ok = bool(analytics_tok and analytics_tok.get("refresh_token"))
    return {"data": data_ok, "analytics": analytics_ok}


# ── Session management (M3.5) ─────────────────────────────────────────────────

def new_session_id() -> str:
    return secrets.token_hex(32)


async def create_session(metadata: Optional[dict] = None) -> str:
    """
    Create a new session, store it in Upstash with TTL, and return the *signed*
    cookie value (`{sid}.{hmac}`). The bare session ID is the Upstash key; the
    signature lives only in the cookie (M4 item 5).
    """
    sid = new_session_id()
    payload = {"created": int(time.time()), **(metadata or {})}
    await _set(f"{SESSION_PREFIX}{sid}", json.dumps(payload), ex=SESSION_TTL)
    return _sign_sid(sid)


async def get_session(cookie_value: str) -> Optional[dict]:
    """
    Return session data for a signed cookie value, or None. The HMAC is verified
    before Upstash is queried — tampered/unsigned cookies are rejected outright.
    """
    sid = _unsign_value(cookie_value)
    if sid is None:
        return None
    raw = await _get(f"{SESSION_PREFIX}{sid}")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def delete_session(cookie_value: str) -> None:
    sid = _unsign_value(cookie_value)
    if sid is None:
        return  # nothing valid to delete
    await _del(f"{SESSION_PREFIX}{sid}")
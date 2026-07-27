"""
googleauth.py — Google OAuth2 client factory with Upstash-backed token refresh.
Port of googleAuth.ts (M3.4) adapted for Python / FastAPI context.

Flow:
  1. /api/auth/start?account=data|analytics  → consent URL → redirect
  2. /api/auth/callback                       → exchange code → Upstash → session cookie
  3. Pipeline calls build_authed_credentials(account) → google.oauth2.credentials.Credentials
     with auto-refresh that writes back to Upstash (D10).

M7 fix: gsc_client() wraps gapi_build() in asyncio.to_thread() so the
googleapiclient discovery-document HTTP call does not block the event loop.
gapi_build() fetches https://www.googleapis.com/.../searchconsole/v1/rest on
every call when the document is not in its in-process cache. That HTTP call is
synchronous and blocking — running it directly inside an async function blocks
all other coroutines on the event loop until it completes, making the 13+ chunk
tasks for a heavy domain effectively sequential instead of concurrent.
"""
from __future__ import annotations
import asyncio
import os
import time
from typing import Optional
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build as gapi_build

from .tokenstore import get_token, set_token, AccountKey

GSC_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

ACCOUNT_SCOPES: dict[str, list[str]] = {
    "data": [*GSC_SCOPES, *DRIVE_SCOPES],
    "analytics": [*GSC_SCOPES],
}


def _require_env(name: str) -> str:
    v = os.environ.get(name, "")
    if not v:
        raise RuntimeError(f"{name} is not set.")
    return v


def app_base_url() -> str:
    return _require_env("APP_BASE_URL").rstrip("/")


def redirect_uri() -> str:
    return f"{app_base_url()}/api/auth/callback"


def _client_config() -> dict:
    return {
        "web": {
            "client_id": _require_env("GOOGLE_CLIENT_ID"),
            "client_secret": _require_env("GOOGLE_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri()],
        }
    }


def build_auth_url(account: AccountKey) -> str:
    """Build the Google consent URL for the given account key."""
    flow = Flow.from_client_config(
        _client_config(),
        scopes=ACCOUNT_SCOPES[account],
        redirect_uri=redirect_uri(),
    )
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
        state=account,
    )
    return url


def _exchange_code_sync(code: str, account: AccountKey) -> dict:
    """
    Synchronous token exchange — runs in a thread pool via asyncio.to_thread().
    google_auth_oauthlib.Flow.fetch_token() is a blocking HTTP call and must
    never be called directly inside an async function (blocks the event loop).
    """
    flow = Flow.from_client_config(
        _client_config(),
        scopes=ACCOUNT_SCOPES[account],
        redirect_uri=redirect_uri(),
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    token = {
        "access_token": creds.token or "",
        "refresh_token": creds.refresh_token or "",
        "expiry_date": int(creds.expiry.timestamp() * 1000) if creds.expiry else 0,
        "scope": " ".join(creds.scopes or []),
        "token_type": "Bearer",
    }
    if not token["refresh_token"]:
        raise ValueError("no_refresh_token")
    return token


async def exchange_code(code: str, account: AccountKey) -> dict:
    """
    Exchange authorization code for tokens, persist to Upstash, return token dict.
    fetch_token() is blocking — offloaded to a thread pool via asyncio.to_thread().
    """
    token = await asyncio.to_thread(_exchange_code_sync, code, account)
    await set_token(account, token)
    return token


def _refresh_credentials_sync(creds: Credentials) -> Credentials:
    """Blocking credential refresh — run via asyncio.to_thread()."""
    import google.auth.transport.requests
    request = google.auth.transport.requests.Request()
    creds.refresh(request)
    return creds


async def build_authed_credentials(account: AccountKey) -> Credentials:
    """
    Build a google.oauth2.credentials.Credentials object from the stored token.
    If the access token is expired, refresh it and write the new token back to
    Upstash (D10 — token refresh must write back to Upstash).
    """
    stored = await get_token(account)
    if not stored or not stored.get("refresh_token"):
        raise RuntimeError(
            f"Account {account}@ is not connected. Open /setup and connect it before running."
        )

    creds = Credentials(
        token=stored.get("access_token"),
        refresh_token=stored["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=_require_env("GOOGLE_CLIENT_ID"),
        client_secret=_require_env("GOOGLE_CLIENT_SECRET"),
        scopes=ACCOUNT_SCOPES[account],
    )

    # Check expiry and refresh proactively (D10).
    expiry_ms = stored.get("expiry_date", 0)
    now_ms = int(time.time() * 1000)
    if expiry_ms and now_ms >= expiry_ms - 60_000:
        # Blocking refresh — offloaded to thread pool (D10 + event loop safety)
        creds = await asyncio.to_thread(_refresh_credentials_sync, creds)
        await set_token(account, {
            "access_token": creds.token or "",
            "refresh_token": creds.refresh_token or stored["refresh_token"],
            "expiry_date": int(creds.expiry.timestamp() * 1000) if creds.expiry else 0,
            "scope": stored.get("scope", ""),
            "token_type": "Bearer",
        })

    return creds


def _build_gsc_client_sync(creds: Credentials):
    """
    Synchronous googleapiclient build call — run via asyncio.to_thread().

    gapi_build() fetches the API discovery document over HTTP on every call
    when the document is not already in its in-process cache. That HTTP round-trip
    is blocking and must not run on the event loop — doing so blocks all other
    concurrent coroutines until it completes, making the chunk tasks for heavy
    domains effectively sequential. Offloading to a thread pool keeps the event
    loop free for other chunk tasks to proceed concurrently.
    """
    return gapi_build("searchconsole", "v1", credentials=creds)


async def gsc_client(account: AccountKey):
    """Return an authenticated Search Console API client."""
    creds = await build_authed_credentials(account)
    # M7 fix: offload gapi_build() to a thread pool so the discovery-document
    # HTTP call does not block the event loop (see _build_gsc_client_sync above).
    return await asyncio.to_thread(_build_gsc_client_sync, creds)


def _build_drive_client_sync(creds: Credentials):
    """Synchronous Drive client build — run via asyncio.to_thread()."""
    return gapi_build("drive", "v3", credentials=creds)


async def drive_client():
    """Return an authenticated Drive API client (data@ account only)."""
    creds = await build_authed_credentials("data")
    return await asyncio.to_thread(_build_drive_client_sync, creds)

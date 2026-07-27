"""
gate.py — Application access gate (M7).

Replaces the M5 Basic Auth middleware (browser popup) with a styled login page
backed by a single shared username/password. A correct sign-in issues an
HMAC-signed cookie; every protected route checks that cookie before serving.

Design notes:
  - Credentials live ONLY in APP_GATE_USER / APP_GATE_PASS (env). Never logged,
    never returned in a response, never committed.
  - The session cookie carries no secret — only a signed, expiring token. The
    signature is verified in constant time before the cookie is trusted, so a
    tampered or expired cookie is rejected without granting access.
  - When APP_GATE_USER / APP_GATE_PASS are unset the gate is disabled (local dev
    runs straight through), mirroring the old fail-open-on-missing-config rule
    but WITHOUT the old fail-open-on-missing-Redis behavior — there is no Redis
    dependency here at all.
"""
from __future__ import annotations
import hmac
import hashlib
import base64
import time

from .config import GATE_USER, GATE_PASS, GATE_SECRET, GATE_ENABLED, GATE_SESSION_MAX_AGE

GATE_COOKIE = "ffg_gate"


def _secret() -> bytes:
    # Fall back to a value derived from the credentials so the gate still works
    # if APP_GATE_SECRET was not set, while staying stable across restarts.
    raw = GATE_SECRET or f"{GATE_USER}:{GATE_PASS}"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _sign(message: str) -> str:
    sig = hmac.new(_secret(), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")


def credentials_match(user: str, password: str) -> bool:
    """Constant-time comparison of supplied credentials against the configured pair."""
    if not GATE_ENABLED:
        return True
    u_ok = hmac.compare_digest(user.encode("utf-8"), GATE_USER.encode("utf-8"))
    p_ok = hmac.compare_digest(password.encode("utf-8"), GATE_PASS.encode("utf-8"))
    # Evaluate both sides regardless of the first result (no short-circuit).
    return u_ok and p_ok


def issue_cookie() -> str:
    """Create a signed, expiring session token: '<expiry>.<signature>'."""
    expiry = str(int(time.time()) + GATE_SESSION_MAX_AGE)
    return f"{expiry}.{_sign(expiry)}"


def cookie_valid(value: str | None) -> bool:
    """Verify a gate cookie's signature and expiry in constant time."""
    if not GATE_ENABLED:
        return True
    if not value or "." not in value:
        return False
    expiry, _, sig = value.partition(".")
    expected = _sign(expiry)
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(expiry) > int(time.time())
    except ValueError:
        return False

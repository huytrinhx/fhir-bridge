"""Google OAuth2 authorization-code flow.

Built and testable end to end except for the final live round trip, which
needs real GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET from a Google Cloud Console
OAuth client (redirect URI to register there:
http://localhost:8000/api/auth/google/callback).
"""
from __future__ import annotations

import os
import secrets
import time
from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

STATE_TTL_SECONDS = 600

# In-memory CSRF state store -- short-lived, single-process, matches the
# rest of this app's "in-memory is fine at local-dev scale" pattern (see
# backend/api.py's _sessions dict).
_pending_states: dict[str, float] = {}


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")


def _client_id() -> str:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise RuntimeError("GOOGLE_CLIENT_ID is not set")
    return client_id


def _client_secret() -> str:
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_secret:
        raise RuntimeError("GOOGLE_CLIENT_SECRET is not set")
    return client_secret


def _sweep_expired_states() -> None:
    now = time.time()
    expired = [s for s, issued_at in _pending_states.items() if now - issued_at > STATE_TTL_SECONDS]
    for s in expired:
        del _pending_states[s]


def build_authorize_url() -> str:
    _sweep_expired_states()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()

    params = {
        "client_id": _client_id(),
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def consume_state(state: str) -> bool:
    """Returns True iff state was a state we issued -- pops it either way
    (single use)."""
    _sweep_expired_states()
    return _pending_states.pop(state, None) is not None


def exchange_code_for_userinfo(code: str) -> dict:
    """Exchanges an authorization code for the user's Google profile.

    Returns {"sub": ..., "email": ..., "name": ...}.
    """
    token_response = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "redirect_uri": _redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    token_response.raise_for_status()
    access_token = token_response.json()["access_token"]

    userinfo_response = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    userinfo_response.raise_for_status()
    data = userinfo_response.json()

    return {"sub": data["sub"], "email": data.get("email"), "name": data.get("name")}

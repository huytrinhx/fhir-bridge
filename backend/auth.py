"""Password hashing + JWT session tokens.

Bare-bones by design (per explicit scope decision): no password reset, no
email verification, no refresh-token rotation -- a single 7-day token,
re-login after it expires.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

TOKEN_TTL = timedelta(days=7)
JWT_ALGORITHM = "HS256"


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY is not set")
    return key


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + TOKEN_TTL,
    }
    return jwt.encode(payload, _secret_key(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    try:
        payload: dict[str, Any] = jwt.decode(token, _secret_key(), algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("sub")


def user_id_from_header(authorization: str | None) -> str | None:
    """Returns the user id from a "Bearer <token>" header, or None for
    missing/malformed/expired tokens -- callers treat None as guest, not an
    error, since guest is a legitimate first-class state for most routes."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        return None
    return decode_access_token(token)


def is_admin_email(username: str | None, email: str | None, admin_email: str | None) -> bool:
    """There's exactly one admin, identified by matching ADMIN_EMAIL (see
    backend/config.py) against either the account's email or its username --
    a password-signup account has no required email field (see
    frontend/src/components/AuthPanel.tsx), so someone who types their email
    into the username box instead is still recognized. Case-insensitive
    since email providers treat case as insignificant for the local part in
    practice and a user shouldn't lose admin access over capitalization."""
    if not admin_email:
        return False
    admin_email = admin_email.strip().lower()
    if email and email.strip().lower() == admin_email:
        return True
    if username and username.strip().lower() == admin_email:
        return True
    return False

"""Postgres-backed conversation log: persisted run history + feedback reports
+ user accounts.

Same connection style as backend/retrieval.py (plain psycopg.connect() per
call, no pool) -- consistent with the rest of this codebase, and fine at
local-dev scale.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.config import Settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    email TEXT UNIQUE,
    username TEXT UNIQUE,
    password_hash TEXT,
    google_sub TEXT UNIQUE,
    display_name TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    initial_message TEXT NOT NULL,
    data_format TEXT,
    terminology_system TEXT,
    data_sample TEXT,
    model TEXT NOT NULL,
    outcome_kind TEXT,
    transcript JSONB NOT NULL,
    display_transcript JSONB NOT NULL,
    last_outcome JSONB
);

-- Added after the table already existed in earlier phases -- IF NOT EXISTS
-- so existing local data survives instead of needing a drop/recreate.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);

CREATE INDEX IF NOT EXISTS conversations_created_at_idx ON conversations (created_at DESC);
CREATE INDEX IF NOT EXISTS conversations_user_id_idx ON conversations (user_id);

CREATE TABLE IF NOT EXISTS feedback_reports (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_expectation TEXT NOT NULL,
    transcript_snapshot JSONB NOT NULL,
    outcome_snapshot JSONB
);

ALTER TABLE feedback_reports ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);
CREATE INDEX IF NOT EXISTS feedback_reports_user_id_idx ON feedback_reports (user_id);
"""

INSERT_PASSWORD_USER_SQL = """
INSERT INTO users (id, username, email, password_hash, display_name)
VALUES (%(id)s, %(username)s, %(email)s, %(password_hash)s, %(username)s)
RETURNING id, username, email, display_name;
"""

SELECT_USER_BY_USERNAME_SQL = """
SELECT id, username, email, password_hash, display_name
FROM users WHERE username = %(username)s;
"""

SELECT_USER_BY_ID_SQL = """
SELECT id, username, email, display_name FROM users WHERE id = %(id)s;
"""

SELECT_USER_BY_GOOGLE_SUB_SQL = """
SELECT id, username, email, display_name FROM users WHERE google_sub = %(google_sub)s;
"""

SELECT_USER_BY_EMAIL_SQL = """
SELECT id, username, email, display_name FROM users WHERE email = %(email)s;
"""

LINK_GOOGLE_SUB_SQL = """
UPDATE users SET google_sub = %(google_sub)s WHERE id = %(id)s
RETURNING id, username, email, display_name;
"""

INSERT_GOOGLE_USER_SQL = """
INSERT INTO users (id, email, google_sub, display_name)
VALUES (%(id)s, %(email)s, %(google_sub)s, %(display_name)s)
RETURNING id, username, email, display_name;
"""

UPSERT_CONVERSATION_SQL = """
INSERT INTO conversations
    (id, user_id, initial_message, data_format, terminology_system, data_sample, model,
     outcome_kind, transcript, display_transcript, last_outcome, updated_at)
VALUES
    (%(id)s, %(user_id)s, %(initial_message)s, %(data_format)s, %(terminology_system)s, %(data_sample)s,
     %(model)s, %(outcome_kind)s, %(transcript)s, %(display_transcript)s, %(last_outcome)s, now())
ON CONFLICT (id) DO UPDATE SET
    outcome_kind = EXCLUDED.outcome_kind,
    transcript = EXCLUDED.transcript,
    display_transcript = EXCLUDED.display_transcript,
    last_outcome = EXCLUDED.last_outcome,
    updated_at = now();
"""

DEFAULT_LIST_LIMIT = 10
# Deep search intentionally isn't capped to the same 10 as the default view --
# it exists precisely to look further back than that.
SEARCH_RESULT_LIMIT = 200

LIST_CONVERSATIONS_SQL = """
SELECT id, created_at, initial_message, model, outcome_kind
FROM conversations
WHERE user_id = %(user_id)s
ORDER BY created_at DESC
LIMIT %(limit)s;
"""

# ~* is Postgres's case-insensitive POSIX regex match. The pattern is a bound
# query parameter, not interpolated SQL, so this is not injectable -- an
# invalid pattern raises psycopg.errors.InvalidRegularExpression, which the
# API layer turns into a 400 rather than a raw 500.
SEARCH_CONVERSATIONS_SQL = """
SELECT id, created_at, initial_message, model, outcome_kind
FROM conversations
WHERE user_id = %(user_id)s AND initial_message ~* %(pattern)s
ORDER BY created_at DESC
LIMIT %(limit)s;
"""

GET_CONVERSATION_SQL = """
SELECT id, created_at, initial_message, data_format, terminology_system, data_sample,
       model, outcome_kind, display_transcript, last_outcome
FROM conversations
WHERE id = %(id)s AND user_id = %(user_id)s;
"""

INSERT_FEEDBACK_SQL = """
INSERT INTO feedback_reports (id, conversation_id, user_id, user_expectation, transcript_snapshot, outcome_snapshot)
VALUES (%(id)s, %(conversation_id)s, %(user_id)s, %(user_expectation)s, %(transcript_snapshot)s, %(outcome_snapshot)s)
RETURNING id, created_at;
"""

LIST_FEEDBACK_SQL = """
SELECT f.id, f.created_at, f.conversation_id, f.user_expectation, c.initial_message, c.model
FROM feedback_reports f
JOIN conversations c ON c.id = f.conversation_id
WHERE f.user_id = %(user_id)s
ORDER BY f.created_at DESC
LIMIT %(limit)s;
"""

GET_FEEDBACK_SQL = """
SELECT f.id, f.created_at, f.conversation_id, f.user_expectation,
       f.transcript_snapshot, f.outcome_snapshot, c.initial_message, c.model
FROM feedback_reports f
JOIN conversations c ON c.id = f.conversation_id
WHERE f.id = %(id)s AND f.user_id = %(user_id)s;
"""


def get_connection(settings: Settings) -> psycopg.Connection:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(settings.database_url, row_factory=dict_row)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()


def create_password_user(
    conn: psycopg.Connection, *, username: str, password_hash: str, email: str | None
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            INSERT_PASSWORD_USER_SQL,
            {"id": str(uuid.uuid4()), "username": username, "email": email, "password_hash": password_hash},
        )
        conn.commit()
        return cur.fetchone()


def get_user_by_username(conn: psycopg.Connection, username: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(SELECT_USER_BY_USERNAME_SQL, {"username": username})
        return cur.fetchone()


def get_user_by_id(conn: psycopg.Connection, user_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(SELECT_USER_BY_ID_SQL, {"id": user_id})
        return cur.fetchone()


def get_or_create_google_user(
    conn: psycopg.Connection, *, google_sub: str, email: str | None, display_name: str | None
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(SELECT_USER_BY_GOOGLE_SUB_SQL, {"google_sub": google_sub})
        existing = cur.fetchone()
        if existing:
            return existing

        # A password account with this email already exists -- link Google
        # to it rather than creating a second, disconnected account.
        if email:
            cur.execute(SELECT_USER_BY_EMAIL_SQL, {"email": email})
            by_email = cur.fetchone()
            if by_email:
                cur.execute(LINK_GOOGLE_SUB_SQL, {"google_sub": google_sub, "id": by_email["id"]})
                conn.commit()
                return cur.fetchone()

        cur.execute(
            INSERT_GOOGLE_USER_SQL,
            {"id": str(uuid.uuid4()), "email": email, "google_sub": google_sub, "display_name": display_name},
        )
        conn.commit()
        return cur.fetchone()


def save_conversation(
    conn: psycopg.Connection,
    *,
    conversation_id: str,
    user_id: str,
    initial_message: str,
    data_format: str | None,
    terminology_system: str | None,
    data_sample: str | None,
    model: str,
    outcome_kind: str,
    transcript: list[dict],
    display_transcript: list[dict],
    last_outcome: dict | None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            UPSERT_CONVERSATION_SQL,
            {
                "id": conversation_id,
                "user_id": user_id,
                "initial_message": initial_message,
                "data_format": data_format,
                "terminology_system": terminology_system,
                "data_sample": data_sample,
                "model": model,
                "outcome_kind": outcome_kind,
                "transcript": json.dumps(transcript),
                "display_transcript": json.dumps(display_transcript),
                "last_outcome": json.dumps(last_outcome) if last_outcome is not None else None,
            },
        )
    conn.commit()


def list_conversations(
    conn: psycopg.Connection,
    user_id: str,
    limit: int = DEFAULT_LIST_LIMIT,
    search: str | None = None,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        if search:
            cur.execute(
                SEARCH_CONVERSATIONS_SQL,
                {"user_id": user_id, "pattern": search, "limit": SEARCH_RESULT_LIMIT},
            )
        else:
            cur.execute(LIST_CONVERSATIONS_SQL, {"user_id": user_id, "limit": limit})
        return cur.fetchall()


def get_conversation(conn: psycopg.Connection, conversation_id: str, user_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(GET_CONVERSATION_SQL, {"id": conversation_id, "user_id": user_id})
        return cur.fetchone()


def save_feedback(
    conn: psycopg.Connection,
    *,
    conversation_id: str,
    user_id: str,
    user_expectation: str,
    transcript_snapshot: list[dict],
    outcome_snapshot: dict | None,
) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute(
            INSERT_FEEDBACK_SQL,
            {
                "id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "user_id": user_id,
                "user_expectation": user_expectation,
                "transcript_snapshot": json.dumps(transcript_snapshot),
                "outcome_snapshot": json.dumps(outcome_snapshot) if outcome_snapshot is not None else None,
            },
        )
        conn.commit()
        return cur.fetchone()


def list_feedback(conn: psycopg.Connection, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(LIST_FEEDBACK_SQL, {"user_id": user_id, "limit": limit})
        return cur.fetchall()


def get_feedback(conn: psycopg.Connection, feedback_id: str, user_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(GET_FEEDBACK_SQL, {"id": feedback_id, "user_id": user_id})
        return cur.fetchone()

"""HTTP API over FhirBridgeSession for the frontend chat UI.

One endpoint drives the whole conversation: POST /api/messages. The caller
passes session_id=null on the first message (creates a session, calls
start() with the composed use case + optional data/format/terminology/model);
on every message after, it passes the session_id it got back (calls
respond() against that session's pending clarifying question).

Two usage modes, chosen on the frontend's landing page:
  - Guest (no Authorization header): the live session exists only in the
    in-memory dicts below for the lifetime of the process, swept out after
    20 minutes idle -- nothing is ever written to Postgres.
  - Authenticated (Authorization: Bearer <jwt>): every turn is persisted,
    scoped to that user_id, with PHI-shaped content in the user's own text
    redacted (backend/phi_redaction.py) before it's sent to the LLM *and*
    before it's persisted.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict

import anthropic
import psycopg
from fastapi import FastAPI, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from backend import auth, google_oauth, persistence
from backend.agent import (
    ClarifyingQuestion,
    FhirBridgeSession,
    FinalRecommendation,
    OutOfScope,
    build_display_transcript,
    compose_use_case,
    serialize_messages,
)
from backend.config import load_settings
from backend.models import list_available_models
from backend.notifications import notify_feedback_submitted
from backend.phi_redaction import redact_phi

FRONTEND_ORIGIN = "http://localhost:5173"
GUEST_IDLE_TIMEOUT_SECONDS = 20 * 60
SWEEP_INTERVAL_SECONDS = 120

_sessions: dict[str, FhirBridgeSession] = {}
_session_models: dict[str, str] = {}
_session_last_active: dict[str, float] = {}


async def _sweep_idle_sessions() -> None:
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        now = time.time()
        stale_ids = [
            sid
            for sid, last_active in _session_last_active.items()
            if now - last_active > GUEST_IDLE_TIMEOUT_SECONDS
        ]
        for sid in stale_ids:
            _sessions.pop(sid, None)
            _session_models.pop(sid, None)
            _session_last_active.pop(sid, None)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sweep_task = asyncio.create_task(_sweep_idle_sessions())
    yield
    sweep_task.cancel()


app = FastAPI(title="FHIR Bud API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class MessageRequest(BaseModel):
    session_id: str | None = None
    message: str
    # Only meaningful when session_id is None (first turn of a conversation).
    data_sample: str | None = None
    data_format: str | None = None
    terminology_system: str | None = None
    model: str | None = None


class FeedbackRequest(BaseModel):
    conversation_id: str
    user_expectation: str


class SignupRequest(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


def _public_user(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "username": row.get("username"),
        "email": row.get("email"),
        "display_name": row.get("display_name"),
    }


def _require_user_id(authorization: str | None) -> str:
    user_id = auth.user_id_from_header(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user_id


def _serialize_outcome(outcome) -> dict:
    if isinstance(outcome, OutOfScope):
        return {"kind": "out_of_scope", "reason": outcome.reason}
    if isinstance(outcome, ClarifyingQuestion):
        return {"kind": "clarifying_question", "questions": outcome.questions}
    if isinstance(outcome, FinalRecommendation):
        return {
            "kind": "final_recommendation",
            "must_have": [asdict(r) for r in outcome.must_have],
            "potentially_needed": [asdict(r) for r in outcome.potentially_needed],
            "dropped": outcome.dropped,
            "redacted": outcome.redacted,
        }
    raise AssertionError(f"unhandled outcome type: {outcome!r}")


def _persist(
    conversation_id: str,
    session: FhirBridgeSession,
    outcome,
    *,
    user_id: str,
    initial_message: str,
    data_format: str | None,
    terminology_system: str | None,
    data_sample: str | None,
    model: str,
) -> None:
    outcome_payload = _serialize_outcome(outcome)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        persistence.save_conversation(
            conn,
            conversation_id=conversation_id,
            user_id=user_id,
            initial_message=initial_message,
            data_format=data_format,
            terminology_system=terminology_system,
            data_sample=data_sample,
            model=model,
            outcome_kind=outcome_payload["kind"],
            transcript=serialize_messages(session.messages),
            display_transcript=build_display_transcript(session.messages),
            last_outcome=outcome_payload,
        )
    finally:
        conn.close()


async def _run_agent_call(fn, *args) -> object:
    try:
        return await run_in_threadpool(fn, *args)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except anthropic.APIError as exc:
        # Full upstream detail (raw provider error JSON) goes to the server
        # log, not the client -- it's noisy/technical and not actionable for
        # an end user picking a model from a dropdown.
        print(f"[api] model call failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail=(
                "This model couldn't complete the request -- it may not support the "
                "tool-calling this app requires, or is temporarily unavailable. "
                "Try a different model."
            ),
        ) from exc


@app.post("/api/messages")
async def post_message(req: MessageRequest, authorization: str | None = Header(None)) -> dict:
    user_id = auth.user_id_from_header(authorization)

    if req.session_id is None:
        settings = load_settings()
        conversation_id = str(uuid.uuid4())
        model = req.model or settings.synth_model

        message = req.message
        data_sample = req.data_sample
        if user_id:
            message, _ = redact_phi(message)
            if data_sample:
                data_sample, _ = redact_phi(data_sample)

        session = FhirBridgeSession(settings, synth_model=model)
        initial_message = compose_use_case(message, data_sample, req.data_format, req.terminology_system)
        outcome = await _run_agent_call(session.start, initial_message)

        _sessions[conversation_id] = session
        _session_models[conversation_id] = model
        _session_last_active[conversation_id] = time.time()

        if user_id:
            _persist(
                conversation_id,
                session,
                outcome,
                user_id=user_id,
                initial_message=message,
                data_format=req.data_format,
                terminology_system=req.terminology_system,
                data_sample=data_sample,
                model=model,
            )
        return {"session_id": conversation_id, **_serialize_outcome(outcome)}

    session = _sessions.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session_id")

    message = req.message
    if user_id:
        message, _ = redact_phi(message)

    outcome = await _run_agent_call(session.respond, message)
    _session_last_active[req.session_id] = time.time()

    if user_id:
        # initial_message/data_format/terminology_system/data_sample are only
        # written on the first insert -- save_conversation's ON CONFLICT clause
        # never touches them again, so these are unused placeholders here.
        _persist(
            req.session_id,
            session,
            outcome,
            user_id=user_id,
            initial_message="",
            data_format=None,
            terminology_system=None,
            data_sample=None,
            model=_session_models.get(req.session_id, load_settings().synth_model),
        )
    return {"session_id": req.session_id, **_serialize_outcome(outcome)}


@app.get("/api/models")
def get_models() -> dict:
    settings = load_settings()
    return {"models": list_available_models(settings)}


@app.post("/api/auth/signup")
def signup(req: SignupRequest) -> dict:
    if not req.username.strip() or not req.password:
        raise HTTPException(status_code=400, detail="username and password are required")

    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        if persistence.get_user_by_username(conn, req.username):
            raise HTTPException(status_code=409, detail="username already taken")
        user = persistence.create_password_user(
            conn,
            username=req.username,
            password_hash=auth.hash_password(req.password),
            email=req.email,
        )
    finally:
        conn.close()

    token = auth.create_access_token(str(user["id"]))
    return {"token": token, "user": _public_user(user)}


@app.post("/api/auth/login")
def login(req: LoginRequest) -> dict:
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        user = persistence.get_user_by_username(conn, req.username)
    finally:
        conn.close()

    if user is None or not user.get("password_hash") or not auth.verify_password(
        req.password, user["password_hash"]
    ):
        raise HTTPException(status_code=401, detail="invalid username or password")

    token = auth.create_access_token(str(user["id"]))
    return {"token": token, "user": _public_user(user)}


@app.get("/api/auth/google/login")
def google_login() -> RedirectResponse:
    return RedirectResponse(google_oauth.build_authorize_url())


@app.get("/api/auth/google/callback")
def google_callback(code: str, state: str) -> RedirectResponse:
    if not google_oauth.consume_state(state):
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")

    profile = google_oauth.exchange_code_for_userinfo(code)

    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        user = persistence.get_or_create_google_user(
            conn,
            google_sub=profile["sub"],
            email=profile.get("email"),
            display_name=profile.get("name"),
        )
    finally:
        conn.close()

    token = auth.create_access_token(str(user["id"]))
    return RedirectResponse(f"{FRONTEND_ORIGIN}/?auth_token={token}")


@app.get("/api/auth/me")
def get_me(authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        user = persistence.get_user_by_id(conn, user_id)
    finally:
        conn.close()
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return _public_user(user)


@app.get("/api/conversations")
def get_conversations(search: str | None = None, authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        try:
            rows = persistence.list_conversations(conn, user_id, search=search)
        except psycopg.errors.InvalidRegularExpression as exc:
            conn.rollback()  # failed statement leaves the transaction unusable until rolled back
            raise HTTPException(status_code=400, detail=f"Invalid regex: {exc}") from exc
    finally:
        conn.close()
    return {"conversations": rows}


@app.get("/api/conversations/{conversation_id}")
def get_conversation_detail(conversation_id: str, authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        row = persistence.get_conversation(conn, conversation_id, user_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown conversation_id")
    return row


@app.post("/api/conversations/{conversation_id}/rerun")
async def rerun_conversation(conversation_id: str, authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        row = persistence.get_conversation(conn, conversation_id, user_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown conversation_id")

    new_id = str(uuid.uuid4())
    model = row["model"] or settings.synth_model
    session = FhirBridgeSession(settings, synth_model=model)
    initial_message = compose_use_case(
        row["initial_message"], row["data_sample"], row["data_format"], row["terminology_system"]
    )
    outcome = await _run_agent_call(session.start, initial_message)

    _sessions[new_id] = session
    _session_models[new_id] = model
    _session_last_active[new_id] = time.time()
    _persist(
        new_id,
        session,
        outcome,
        user_id=user_id,
        initial_message=row["initial_message"],
        data_format=row["data_format"],
        terminology_system=row["terminology_system"],
        data_sample=row["data_sample"],
        model=model,
    )
    return {"session_id": new_id, **_serialize_outcome(outcome)}


@app.post("/api/feedback")
def post_feedback(req: FeedbackRequest, authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        row = persistence.get_conversation(conn, req.conversation_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown conversation_id")
        result = persistence.save_feedback(
            conn,
            conversation_id=req.conversation_id,
            user_id=user_id,
            user_expectation=req.user_expectation,
            transcript_snapshot=row["display_transcript"],
            outcome_snapshot=row["last_outcome"],
        )
    finally:
        conn.close()

    try:
        notify_feedback_submitted(
            feedback_id=str(result["id"]),
            initial_message=row["initial_message"],
            user_expectation=req.user_expectation,
        )
    except Exception as exc:  # noqa: BLE001 -- notification is best-effort; the report is already saved
        print(f"[api] feedback notification failed: {exc}")
    return result


@app.get("/api/feedback")
def get_feedback_list(authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        rows = persistence.list_feedback(conn, user_id)
    finally:
        conn.close()
    return {"reports": rows}


@app.get("/api/feedback/{feedback_id}")
def get_feedback_detail(feedback_id: str, authorization: str | None = Header(None)) -> dict:
    user_id = _require_user_id(authorization)
    settings = load_settings()
    conn = persistence.get_connection(settings)
    try:
        row = persistence.get_feedback(conn, feedback_id, user_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown feedback id")
    return row

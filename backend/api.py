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
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

import anthropic
import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import auth, event_stream, google_oauth, persistence
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
from backend.guardrails import load_whitelist
from backend.mapping import build_resource_mapping
from backend.models import list_available_models
from backend.notifications import notify_feedback_submitted
from backend.phi_redaction import redact_phi

# In local dev the frontend runs on Vite's dev server (5173) while the
# backend runs on 8000, so the OAuth redirect and CORS need to know that's a
# different origin. In production (single Railway service, see Dockerfile)
# the frontend is served by this same app, so FRONTEND_ORIGIN should be set
# to that service's own public URL.
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
GUEST_IDLE_TIMEOUT_SECONDS = 20 * 60
SWEEP_INTERVAL_SECONDS = 120
# ~15-20s per issue #4: frequent enough to beat Railway's idle-connection
# timeout risk (reports converge on 5-15 min) with plenty of margin.
SSE_HEARTBEAT_SECONDS = 15.0

_sessions: dict[str, FhirBridgeSession] = {}
_session_models: dict[str, str] = {}
_session_last_active: dict[str, float] = {}
# (data_format, data_sample) for the live in-flight conversation -- needed by
# the mapping endpoint below. compose_use_case only ever embeds these into
# the composed prompt string, so without this they'd be unrecoverable for a
# guest session (nothing persisted) or for an authenticated session before
# its first _persist() call.
_session_data_samples: dict[str, tuple[str | None, str | None]] = {}
# user_id (None for guests) that created each live session -- lets GET
# /api/messages/events (backend/persistence.py's decision_events, a durable
# table) authorize a poll for a conversation whose first turn hasn't
# finished yet, i.e. before a conversations row exists to check ownership
# against the normal way (see that endpoint).
_session_owners: dict[str, str | None] = {}


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
            session = _sessions.pop(sid, None)
            if session is not None:
                session.close()  # releases the Postgres checkpointer connection, if it opened one
            _session_models.pop(sid, None)
            _session_last_active.pop(sid, None)
            _session_data_samples.pop(sid, None)
            _session_owners.pop(sid, None)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    event_stream.bind_loop(asyncio.get_running_loop())
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
    # Client-generated id for a brand-new conversation's first turn, so the
    # frontend can open GET /api/messages/stream *before* this POST resolves
    # -- the server otherwise only hands back a session_id once the whole
    # (blocking) turn is done, which is too late to watch it live. Only
    # consulted when session_id is None; see post_message's create branch.
    client_session_id: str | None = None
    # No client-supplied model override -- cost-risk control. Every
    # conversation uses the admin-configured default (_resolve_model_defaults),
    # never a per-request choice.


class FeedbackRequest(BaseModel):
    conversation_id: str
    user_expectation: str


class MappingRequest(BaseModel):
    resource_type: str


class AdminSettingsRequest(BaseModel):
    intent_model: str
    synth_model: str


class AdminRerunRequest(BaseModel):
    model: str


class SignupRequest(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


def _public_user(row: dict, settings) -> dict:
    return {
        "id": str(row["id"]),
        "username": row.get("username"),
        "email": row.get("email"),
        "display_name": row.get("display_name"),
        "is_admin": auth.is_admin_email(row.get("username"), row.get("email"), settings.admin_email),
    }


def _require_user_id(authorization: str | None) -> str:
    user_id = auth.user_id_from_header(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user_id


def _require_admin(authorization: str | None, settings) -> str:
    user_id = _require_user_id(authorization)
    conn = persistence.get_connection(settings)
    try:
        user = persistence.get_user_by_id(conn, user_id)
    finally:
        conn.close()
    if user is None or not auth.is_admin_email(user.get("username"), user.get("email"), settings.admin_email):
        raise HTTPException(status_code=403, detail="admin access required")
    return user_id


def _valid_uuid(value: str | None) -> str | None:
    """Canonicalizes a client-supplied session id, or None if it isn't one --
    defends decision_events.session_id (a UUID column) against garbage
    without surfacing a user-facing error; callers fall back to generating
    their own id instead."""
    if value is None:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _resolve_model_defaults(settings) -> tuple[str, str]:
    """(intent_model, synth_model) -- the admin-configured override
    (backend/persistence.py::app_settings) if one has been saved, else the
    KYMA_INTENT_MODEL/KYMA_SYNTH_MODEL env default."""
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        row = persistence.get_app_settings(conn)
    finally:
        conn.close()
    intent_model = (row and row.get("intent_model")) or settings.intent_model
    synth_model = (row and row.get("synth_model")) or settings.synth_model
    return intent_model, synth_model


def _serialize_mapping(mapping) -> dict:
    return {
        "resource_type": mapping.resource_type,
        "skeleton": mapping.skeleton,
        "mapped_fields": [asdict(f) for f in mapping.mapped_fields],
        "unmapped_fields": [asdict(f) for f in mapping.unmapped_fields],
    }


def _serialize_outcome(outcome) -> dict:
    if isinstance(outcome, OutOfScope):
        return {"kind": "out_of_scope", "reason": outcome.reason}
    if isinstance(outcome, ClarifyingQuestion):
        return {
            "kind": "clarifying_question",
            "questions": [{"question": q.question, "options": q.options} for q in outcome.questions],
        }
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
        # an end user, who has no model choice to act on (see MessageRequest).
        print(f"[api] model call failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail=(
                "The model backing this app couldn't complete the request -- it may be "
                "temporarily unavailable. Please try again shortly."
            ),
        ) from exc


@app.post("/api/messages")
async def post_message(req: MessageRequest, authorization: str | None = Header(None)) -> dict:
    user_id = auth.user_id_from_header(authorization)

    if req.session_id is None:
        settings = load_settings()
        conversation_id = _valid_uuid(req.client_session_id) or str(uuid.uuid4())
        default_intent_model, model = _resolve_model_defaults(settings)

        message = req.message
        data_sample = req.data_sample
        if user_id:
            message, _ = redact_phi(message)
            if data_sample:
                data_sample, _ = redact_phi(data_sample)

        # persist=bool(user_id): guests get zero persistence, full stop -- that
        # includes the graph's own checkpointer, not just the conversations
        # table below, so a guest session must never open a Postgres connection.
        session = FhirBridgeSession(
            settings,
            synth_model=model,
            intent_model=default_intent_model,
            persist=bool(user_id),
            session_id=conversation_id,
        )
        initial_message = compose_use_case(message, data_sample, req.data_format, req.terminology_system)
        outcome = await _run_agent_call(session.start, initial_message)

        _sessions[conversation_id] = session
        _session_models[conversation_id] = model
        _session_last_active[conversation_id] = time.time()
        _session_data_samples[conversation_id] = (req.data_format, data_sample)
        _session_owners[conversation_id] = user_id

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


async def _progress_events(session_id: str, is_disconnected) -> AsyncIterator[str]:
    """The actual SSE body, factored out of stream_messages() below so it's
    unit-testable by calling it directly with a fake is_disconnected --
    Starlette's TestClient fully buffers a streamed ASGI response before
    returning it, so it hangs forever against a genuinely never-ending
    response like this one; testing this generator directly sidesteps that
    entirely (see tests/test_api.py).

    is_disconnected is FastAPI's Request.is_disconnected in production --
    threaded through as a parameter rather than closing over `request`
    directly, for exactly that testability.
    """
    queue = event_stream.subscribe(session_id)
    try:
        while True:
            if await is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        event_stream.unsubscribe(session_id, queue)


@app.get("/api/messages/stream")
async def stream_messages(session_id: str, request: Request) -> StreamingResponse:
    """Live per-node status for an in-flight graph run (issue #4) -- sourced
    from the same event_logger.log() calls backend/graph.py's nodes already
    make for decision_events (issue #2), broadcast in-process rather than
    read back from Postgres (backend/event_stream.py). No auth: the
    browser's native EventSource API can't set an Authorization header at
    all, so this endpoint treats session_id as a capability token, exactly
    like every other _sessions lookup in this file (e.g. respond()'s
    session_id lookup above has no ownership check either).

    Callers must subscribe before triggering the graph run they want to
    watch -- publish() is fire-and-forget, nothing is buffered for a session
    with no open connection yet.
    """
    return StreamingResponse(
        _progress_events(session_id, request.is_disconnected),
        media_type="text/event-stream",
        # X-Accel-Buffering: no is cheap insurance against a proxy buffering
        # the heartbeat bytes and silently reintroducing the idle-timeout
        # risk this endpoint exists to avoid, even though Railway's own
        # proxy is confirmed non-buffering (see agents.md).
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_MISSING = object()


@app.get("/api/messages/events")
def list_message_events(session_id: str, after_id: int = 0, authorization: str | None = Header(None)) -> dict:
    """Polling fallback for a dropped SSE connection (issue #4): reads the
    same decision_events rows issue #2 persists. Unlike the live stream
    above, this reads a durable table, so it follows this app's user_id-
    scoped-read pattern (like GET /api/conversations/{id}) rather than the
    capability-token pattern the ephemeral _sessions map uses -- guests
    (no Bearer token) 401 here, same as every other persisted-read endpoint;
    nothing to recover for them anyway, since nothing was ever persisted.
    """
    user_id = _require_user_id(authorization)
    settings = load_settings()

    owner = _session_owners.get(session_id, _MISSING)
    if owner is _MISSING:
        # Not (or no longer) in memory -- e.g. this process restarted since
        # the session was created. Fall back to the persisted conversation
        # row, which by now may exist even if _session_owners doesn't.
        conn = persistence.get_connection(settings)
        try:
            row = persistence.get_conversation(conn, session_id, user_id)
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown session_id")
    elif owner != user_id:
        # 404, not 403 -- don't confirm another user's session_id exists.
        raise HTTPException(status_code=404, detail="unknown session_id")

    conn = persistence.get_connection(settings)
    try:
        events = persistence.list_events(conn, session_id=session_id, after_id=after_id)
    finally:
        conn.close()
    return {"events": events}


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
    return {"token": token, "user": _public_user(user, settings)}


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
    return {"token": token, "user": _public_user(user, settings)}


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
    return _public_user(user, settings)


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
    # Always the current admin default, not whatever model the original
    # conversation happened to use -- rerunning is not a way to keep re-using
    # an old per-conversation choice from before model selection was removed.
    default_intent_model, model = _resolve_model_defaults(settings)
    # persist=True: this route requires auth (_require_user_id above), never
    # reachable by a guest.
    session = FhirBridgeSession(
        settings, synth_model=model, intent_model=default_intent_model, persist=True, session_id=new_id
    )
    initial_message = compose_use_case(
        row["initial_message"], row["data_sample"], row["data_format"], row["terminology_system"]
    )
    outcome = await _run_agent_call(session.start, initial_message)

    _sessions[new_id] = session
    _session_models[new_id] = model
    _session_last_active[new_id] = time.time()
    _session_data_samples[new_id] = (row["data_format"], row["data_sample"])
    _session_owners[new_id] = user_id
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
    settings = load_settings()
    _require_admin(authorization, settings)
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        rows = persistence.list_all_feedback(conn)
    finally:
        conn.close()
    return {"reports": rows}


@app.get("/api/feedback/{feedback_id}")
def get_feedback_detail(feedback_id: str, authorization: str | None = Header(None)) -> dict:
    settings = load_settings()
    _require_admin(authorization, settings)
    conn = persistence.get_connection(settings)
    try:
        row = persistence.get_feedback_by_id(conn, feedback_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown feedback id")
    return row


@app.get("/api/admin/settings")
def get_admin_settings(authorization: str | None = Header(None)) -> dict:
    settings = load_settings()
    _require_admin(authorization, settings)
    intent_model, synth_model = _resolve_model_defaults(settings)
    return {
        "intent_model": intent_model,
        "synth_model": synth_model,
        "models": list_available_models(settings),
    }


@app.post("/api/admin/settings")
def update_admin_settings(req: AdminSettingsRequest, authorization: str | None = Header(None)) -> dict:
    settings = load_settings()
    _require_admin(authorization, settings)

    available = list_available_models(settings)
    if req.intent_model not in available:
        raise HTTPException(status_code=400, detail=f"{req.intent_model!r} is not an available model")
    if req.synth_model not in available:
        raise HTTPException(status_code=400, detail=f"{req.synth_model!r} is not an available model")

    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        result = persistence.save_app_settings(conn, intent_model=req.intent_model, synth_model=req.synth_model)
    finally:
        conn.close()
    return result


@app.post("/api/admin/conversations/{conversation_id}/rerun")
async def admin_rerun_conversation(
    conversation_id: str, req: AdminRerunRequest, authorization: str | None = Header(None)
) -> dict:
    """Diagnostic-only counterpart to /api/conversations/{id}/rerun: lets the
    admin explicitly pick a model to test whether a reported quality issue is
    model-specific. Unlike the public rerun endpoint (which always uses the
    admin-configured default, see MessageRequest), this one exists precisely
    to override that default -- it's gated on admin auth, not exposed to
    regular users, and doesn't touch the cost-risk removal that route enforces.
    Can rerun any user's conversation (get_conversation_by_id is unscoped),
    since the report being investigated may not belong to the admin."""
    settings = load_settings()
    admin_user_id = _require_admin(authorization, settings)

    available = list_available_models(settings)
    if req.model not in available:
        raise HTTPException(status_code=400, detail=f"{req.model!r} is not an available model")

    conn = persistence.get_connection(settings)
    try:
        row = persistence.get_conversation_by_id(conn, conversation_id)
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown conversation_id")

    new_id = str(uuid.uuid4())
    # Intent gate stays on the admin-configured default regardless -- only
    # the synthesis model is under test here.
    default_intent_model, _default_synth_model = _resolve_model_defaults(settings)
    # persist=True: this route requires admin auth (_require_admin above),
    # never reachable by a guest.
    session = FhirBridgeSession(
        settings, synth_model=req.model, intent_model=default_intent_model, persist=True, session_id=new_id
    )
    initial_message = compose_use_case(
        row["initial_message"], row["data_sample"], row["data_format"], row["terminology_system"]
    )
    outcome = await _run_agent_call(session.start, initial_message)

    _sessions[new_id] = session
    _session_models[new_id] = req.model
    _session_last_active[new_id] = time.time()
    _session_data_samples[new_id] = (row["data_format"], row["data_sample"])
    # Persisted under the admin's own account -- this is the admin's
    # diagnostic run, not a modification of the original reporter's history.
    _session_owners[new_id] = admin_user_id
    _persist(
        new_id,
        session,
        outcome,
        user_id=admin_user_id,
        initial_message=row["initial_message"],
        data_format=row["data_format"],
        terminology_system=row["terminology_system"],
        data_sample=row["data_sample"],
        model=req.model,
    )
    return {"session_id": new_id, **_serialize_outcome(outcome)}


@app.post("/api/conversations/{conversation_id}/mapping")
async def get_resource_mapping(
    conversation_id: str, req: MappingRequest, authorization: str | None = Header(None)
) -> dict:
    """Deterministic suggested-payload + segment-mapping lookup for one
    resource card -- no LLM call (see backend/mapping.py). Tries the live
    in-memory session first (works for guests and for an authenticated
    conversation that hasn't necessarily finished persisting yet), falling
    back to the persisted record for a conversation loaded from history,
    which requires auth like every other conversation-history endpoint."""
    live = _session_data_samples.get(conversation_id)
    settings = load_settings()
    if live is not None:
        data_format, data_sample = live
    else:
        user_id = _require_user_id(authorization)
        conn = persistence.get_connection(settings)
        try:
            row = persistence.get_conversation(conn, conversation_id, user_id)
        finally:
            conn.close()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown conversation_id")
        data_format, data_sample = row["data_format"], row["data_sample"]

    whitelist = load_whitelist(settings.whitelist_path)
    if req.resource_type not in whitelist:
        raise HTTPException(status_code=400, detail=f"{req.resource_type!r} is not a valid FHIR R4 resource type")

    conn = persistence.get_connection(settings)
    try:
        mapping = await run_in_threadpool(build_resource_mapping, conn, req.resource_type, data_format, data_sample)
    finally:
        conn.close()
    return _serialize_mapping(mapping)


# Serves the built frontend (frontend/dist, produced by `npm run build`) so a
# single process can host both the API and the UI -- see the root Dockerfile.
# Registered last so it never shadows the /api/* routes above: Starlette
# matches routes in registration order, and this mount's prefix is "/".
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

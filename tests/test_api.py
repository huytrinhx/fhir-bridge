"""backend/api.py's issue #4 additions: GET /api/messages/stream (live
per-node progress) and GET /api/messages/events (the polling fallback for a
dropped stream).

The SSE endpoint is tested by calling backend.api._progress_events directly,
not through TestClient -- Starlette's installed TestClient fully buffers a
streamed ASGI response before returning it from client.stream(), which hangs
forever against a genuinely never-ending response like that endpoint's
heartbeat loop. stream_messages() itself is a 3-line wrapper around
_progress_events() + StreamingResponse specifically so the interesting logic
is testable this way (see that function's docstring).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import api, auth, event_stream, persistence
from backend.config import load_settings


@pytest.fixture
def client():
    with TestClient(api.app) as c:
        yield c
    # _session_owners is module-level state that outlives the TestClient --
    # clear whatever a test added so tests don't leak into each other.
    api._session_owners.clear()


def _token(monkeypatch: pytest.MonkeyPatch, user_id: str) -> str:
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    return auth.create_access_token(user_id)


def _require_db() -> object:
    settings = load_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured for local Postgres")
    return settings


def _create_user(settings) -> str:
    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        user = persistence.create_password_user(
            conn, username=f"test-user-{uuid.uuid4()}", password_hash="x", email=None
        )
        return str(user["id"])
    finally:
        conn.close()


# ---- GET /api/messages/stream -- backend.api._progress_events directly ----


def test_progress_events_delivers_a_published_event_as_a_data_line():
    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        session_id = str(uuid.uuid4())
        subscribed = asyncio.Event()

        async def is_disconnected() -> bool:
            subscribed.set()
            return False

        gen = api._progress_events(session_id, is_disconnected)
        next_task = asyncio.ensure_future(gen.__anext__())
        await subscribed.wait()

        event_stream.publish(session_id, "intent", "start", input_data={"use_case": "x"})
        chunk = await next_task

        assert chunk.startswith("data: ")
        assert '"node_name": "intent"' in chunk
        assert '"event_type": "start"' in chunk

        await gen.aclose()
        assert session_id not in event_stream._subscribers

    asyncio.run(run())


def test_progress_events_heartbeats_within_the_configured_interval(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api, "SSE_HEARTBEAT_SECONDS", 0.02)

    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        session_id = str(uuid.uuid4())

        async def is_disconnected() -> bool:
            return False

        gen = api._progress_events(session_id, is_disconnected)
        chunk = await gen.__anext__()  # nothing published -- must time out into a heartbeat
        assert chunk == ": heartbeat\n\n"

        await gen.aclose()

    asyncio.run(run())


def test_progress_events_stops_and_unsubscribes_on_disconnect():
    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        session_id = str(uuid.uuid4())

        async def is_disconnected() -> bool:
            return True  # disconnected from the first check

        chunks = [chunk async for chunk in api._progress_events(session_id, is_disconnected)]
        assert chunks == []
        assert session_id not in event_stream._subscribers

    asyncio.run(run())


def test_stream_route_is_registered_without_auth_dependency():
    # EventSource can't set an Authorization header at all -- confirm this
    # route declares no Header(...) param (which is how every auth-gated
    # route in this file takes its Bearer token), rather than relying on
    # manual review.
    route = next(r for r in api.app.routes if getattr(r, "path", None) == "/api/messages/stream")
    assert route.dependant.header_params == []


# ---- GET /api/messages/events ----


def test_events_requires_authentication(client):
    session_id = str(uuid.uuid4())
    r = client.get(f"/api/messages/events?session_id={session_id}")
    assert r.status_code == 401


def test_events_404s_for_a_session_owned_by_nobody_known(client, monkeypatch):
    _require_db()
    token = _token(monkeypatch, str(uuid.uuid4()))
    session_id = str(uuid.uuid4())  # never created, not in _session_owners or conversations

    r = client.get(f"/api/messages/events?session_id={session_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_events_404s_when_owned_by_a_different_user(client, monkeypatch):
    session_id = str(uuid.uuid4())
    api._session_owners[session_id] = str(uuid.uuid4())  # some other user
    token = _token(monkeypatch, str(uuid.uuid4()))

    r = client.get(f"/api/messages/events?session_id={session_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


def test_events_returns_rows_via_in_memory_ownership_record(client, monkeypatch):
    settings = _require_db()
    owner_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    api._session_owners[session_id] = owner_id
    token = _token(monkeypatch, owner_id)

    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        persistence.record_event(
            conn, session_id=session_id, node_name="intent", event_type="start", input_data={"x": 1}
        )
    finally:
        conn.close()

    r = client.get(f"/api/messages/events?session_id={session_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) == 1
    assert events[0]["node_name"] == "intent"
    assert events[0]["input_data"] == {"x": 1}


def test_events_falls_back_to_conversations_table_when_not_in_memory(client, monkeypatch):
    settings = _require_db()
    owner_id = _create_user(settings)
    session_id = str(uuid.uuid4())
    # Deliberately NOT in api._session_owners -- simulates a process restart
    # since the session was created; ownership now only exists in the
    # persisted conversations row.
    token = _token(monkeypatch, owner_id)

    conn = persistence.get_connection(settings)
    try:
        persistence.save_conversation(
            conn,
            conversation_id=session_id,
            user_id=owner_id,
            initial_message="test",
            data_format=None,
            terminology_system=None,
            data_sample=None,
            model="test-model",
            outcome_kind="final_recommendation",
            transcript=[],
            display_transcript=[],
            last_outcome=None,
        )
        persistence.record_event(conn, session_id=session_id, node_name="intent", event_type="start")
    finally:
        conn.close()

    r = client.get(f"/api/messages/events?session_id={session_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert len(r.json()["events"]) == 1


def test_events_after_id_excludes_already_seen_rows(client, monkeypatch):
    settings = _require_db()
    owner_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    api._session_owners[session_id] = owner_id
    token = _token(monkeypatch, owner_id)

    conn = persistence.get_connection(settings)
    try:
        persistence.ensure_schema(conn)
        persistence.record_event(conn, session_id=session_id, node_name="intent", event_type="start")
        persistence.record_event(conn, session_id=session_id, node_name="intent", event_type="finish")
    finally:
        conn.close()

    headers = {"Authorization": f"Bearer {token}"}
    first = client.get(f"/api/messages/events?session_id={session_id}", headers=headers).json()["events"]
    assert len(first) == 2

    second = client.get(
        f"/api/messages/events?session_id={session_id}&after_id={first[0]['id']}", headers=headers
    ).json()["events"]
    assert [e["id"] for e in second] == [first[1]["id"]]

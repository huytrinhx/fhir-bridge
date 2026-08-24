"""In-memory, in-process live broadcast of decision-point events, for the SSE
endpoint in backend/api.py (GET /api/messages/stream).

Single Railway replica, no --workers (see root Dockerfile) -- same
single-process constraint agents.md already documents for backend/api.py's
_sessions/_session_models dicts, so a plain in-memory registry here is
consistent, not a new risk.

Self-cleaning: a session's entry only exists while at least one SSE
connection for it is open (subscribe() creates it, unsubscribe() removes it
the instant the last connection for that session closes) -- no sweep-loop
entry needed, unlike _sessions and friends.

Not a replay buffer: publish() with no subscribers is a no-op, nothing is
queued for a session with no open connection. backend/api.py's SSE endpoint
subscribes before the frontend's POST (which starts the graph run) can fire,
so nothing is lost in the window that matters -- see that endpoint's
docstring for the connect-before-fire contract this relies on.
"""
from __future__ import annotations

import asyncio
from typing import Any

from backend.persistence import EventLogger

_loop: asyncio.AbstractEventLoop | None = None
_subscribers: dict[str, set[asyncio.Queue]] = {}


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from backend/api.py's lifespan(), on the event-loop
    thread -- publish() needs this to hand events from a worker thread
    (FhirBridgeSession's graph runs via FastAPI's run_in_threadpool) back to
    the event loop safely."""
    global _loop
    _loop = loop


def subscribe(session_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(session_id, set()).add(queue)
    return queue


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    queues = _subscribers.get(session_id)
    if queues is None:
        return
    queues.discard(queue)
    if not queues:
        _subscribers.pop(session_id, None)


def publish(
    session_id: str, node_name: str, event_type: str, *, input_data: Any = None, output_data: Any = None
) -> None:
    """Thread-safe: called from FhirBridgeSession's threadpool worker thread,
    not the event-loop thread that owns the subscriber queues."""
    queues = _subscribers.get(session_id)
    if not queues or _loop is None:
        return
    event = {
        "node_name": node_name,
        "event_type": event_type,
        "input_data": input_data,
        "output_data": output_data,
    }
    for queue in queues:
        _loop.call_soon_threadsafe(queue.put_nowait, event)


class CompositeEventLogger:
    """Satisfies backend/graph.py's EventLogger Protocol. Always publishes
    live -- guests get zero *persistence* (agents.md), but the SSE stream
    itself is not persistence, so this must fire regardless of persist=True/
    False. Separately delegates to the Postgres-backed logger, which decides
    on its own whether to actually write (a no-op for guests, see
    backend/persistence.py::EventLogger)."""

    def __init__(self, session_id: str, persisted: EventLogger) -> None:
        self._session_id = session_id
        self._persisted = persisted

    def log(self, node_name: str, event_type: str, *, input_data: Any = None, output_data: Any = None) -> None:
        publish(self._session_id, node_name, event_type, input_data=input_data, output_data=output_data)
        self._persisted.log(node_name, event_type, input_data=input_data, output_data=output_data)

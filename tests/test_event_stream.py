"""backend/event_stream.py: in-process pub/sub for GET /api/messages/stream,
and CompositeEventLogger, which bridges backend/graph.py's EventLogger
Protocol to both the live broadcast (always) and Postgres persistence (only
when the wrapped persisted logger is configured to, see
backend/persistence.py::EventLogger).
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from backend import event_stream


class FakePersistedLogger:
    """Stands in for backend/persistence.py's EventLogger -- records calls
    instead of touching Postgres, same spirit as tests/test_graph.py's
    FakeEventLogger."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any, Any]] = []

    def log(self, node_name: str, event_type: str, *, input_data=None, output_data=None) -> None:
        self.calls.append((node_name, event_type, input_data, output_data))


def test_publish_with_no_subscribers_is_a_noop():
    # No subscribe() call for this session_id -- must not raise, must not
    # create an entry in _subscribers, regardless of whether a loop is bound.
    event_stream.publish("no-subscribers", "intent", "start")
    assert "no-subscribers" not in event_stream._subscribers


def test_subscribe_publish_delivers_event_same_thread():
    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        queue = event_stream.subscribe("session-a")
        event_stream.publish("session-a", "intent", "start", input_data={"use_case": "x"})
        # call_soon_threadsafe from the loop's own thread just schedules for
        # the next iteration -- yield control once so it runs.
        await asyncio.sleep(0)
        event = queue.get_nowait()
        assert event == {
            "node_name": "intent",
            "event_type": "start",
            "input_data": {"use_case": "x"},
            "output_data": None,
        }
        event_stream.unsubscribe("session-a", queue)

    asyncio.run(run())


def test_unsubscribe_removes_session_entry_once_last_subscriber_leaves():
    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        q1 = event_stream.subscribe("session-b")
        q2 = event_stream.subscribe("session-b")
        assert "session-b" in event_stream._subscribers

        event_stream.unsubscribe("session-b", q1)
        assert "session-b" in event_stream._subscribers  # q2 still open

        event_stream.unsubscribe("session-b", q2)
        assert "session-b" not in event_stream._subscribers  # self-cleaned

    asyncio.run(run())


def test_publish_delivers_across_threads():
    # The whole reason this module exists: FhirBridgeSession's graph runs on
    # a FastAPI threadpool worker thread, not the event loop thread that owns
    # the subscriber queues -- call_soon_threadsafe is what makes that safe.
    async def run():
        loop = asyncio.get_running_loop()
        event_stream.bind_loop(loop)
        queue = event_stream.subscribe("session-c")

        def publish_from_worker_thread():
            event_stream.publish("session-c", "retrieval", "search_fhir_kb", input_data={"query": "patient"})

        thread = threading.Thread(target=publish_from_worker_thread)
        thread.start()
        thread.join()

        # Give the loop a couple of turns to run the threadsafe callback.
        for _ in range(5):
            if not queue.empty():
                break
            await asyncio.sleep(0)

        event = queue.get_nowait()
        assert event["node_name"] == "retrieval"
        assert event["event_type"] == "search_fhir_kb"
        assert event["input_data"] == {"query": "patient"}

        event_stream.unsubscribe("session-c", queue)

    asyncio.run(run())


def test_publish_reaches_multiple_subscribers():
    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        q1 = event_stream.subscribe("session-d")
        q2 = event_stream.subscribe("session-d")

        event_stream.publish("session-d", "intent", "finish")
        await asyncio.sleep(0)

        assert q1.get_nowait()["node_name"] == "intent"
        assert q2.get_nowait()["node_name"] == "intent"

        event_stream.unsubscribe("session-d", q1)
        event_stream.unsubscribe("session-d", q2)

    asyncio.run(run())


def test_composite_logger_always_publishes_and_always_delegates():
    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        queue = event_stream.subscribe("session-e")
        persisted = FakePersistedLogger()
        logger = event_stream.CompositeEventLogger("session-e", persisted)

        logger.log("reasoning", "start", input_data={"turn_index": 0})
        await asyncio.sleep(0)

        assert queue.get_nowait()["node_name"] == "reasoning"
        assert persisted.calls == [("reasoning", "start", {"turn_index": 0}, None)]

        event_stream.unsubscribe("session-e", queue)

    asyncio.run(run())


def test_composite_logger_publishes_even_when_persisted_logger_is_a_noop():
    # Guest sessions: persistence.EventLogger(settings=None, ...) is a no-op,
    # but the live broadcast must still fire -- the SSE stream itself is not
    # persistence (agents.md).
    class NoOpPersistedLogger:
        def log(self, *args, **kwargs) -> None:
            pass

    async def run():
        event_stream.bind_loop(asyncio.get_running_loop())
        queue = event_stream.subscribe("session-f")
        logger = event_stream.CompositeEventLogger("session-f", NoOpPersistedLogger())

        logger.log("intent", "start", input_data={"use_case": "guest use case"})
        await asyncio.sleep(0)

        assert queue.get_nowait()["node_name"] == "intent"

        event_stream.unsubscribe("session-f", queue)

    asyncio.run(run())

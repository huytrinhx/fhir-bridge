"""Unit-level coverage for backend/langfuse_setup.py that doesn't need real
Langfuse credentials -- mocks the Langfuse client itself, since the real SDK
is exercised only in production/local dev when LANGFUSE_PUBLIC_KEY/
LANGFUSE_SECRET_KEY are actually set.
"""
from unittest.mock import MagicMock, patch

from backend.langfuse_setup import generation_span, get_trace_url, new_callback_handler


def test_get_trace_url_noops_without_a_trace_id():
    with patch("backend.langfuse_setup.get_client") as get_client:
        assert get_trace_url(None) is None
    get_client.assert_not_called()


def test_get_trace_url_returns_none_and_swallows_errors():
    with patch("backend.langfuse_setup.get_client") as get_client:
        get_client.return_value.get_trace_url.side_effect = RuntimeError("boom")
        assert get_trace_url("trace-123") is None


def test_get_trace_url_delegates_to_client():
    with patch("backend.langfuse_setup.get_client") as get_client:
        get_client.return_value.get_trace_url.return_value = "https://cloud.langfuse.com/trace/123"
        assert get_trace_url("trace-123") == "https://cloud.langfuse.com/trace/123"
    get_client.return_value.get_trace_url.assert_called_once_with(trace_id="trace-123")


def test_new_callback_handler_config_fragment_carries_session_and_user():
    with patch("backend.langfuse_setup.CallbackHandler") as handler_cls:
        handler_cls.return_value = MagicMock()
        handler, config_fragment = new_callback_handler(user_id="user-1", session_id="thread-abc")

    assert handler is handler_cls.return_value
    assert config_fragment["callbacks"] == [handler]
    assert config_fragment["metadata"] == {
        "langfuse_session_id": "thread-abc",
        "langfuse_user_id": "user-1",
    }


def test_new_callback_handler_tags_guest_sessions_without_a_user_id():
    with patch("backend.langfuse_setup.CallbackHandler"):
        _, config_fragment = new_callback_handler(user_id=None, session_id="thread-abc")

    assert config_fragment["metadata"]["langfuse_user_id"] == "guest"


def test_generation_span_yields_the_client_generation_observation():
    with patch("backend.langfuse_setup.get_client") as get_client:
        generation = MagicMock()
        get_client.return_value.start_as_current_observation.return_value.__enter__.return_value = generation

        with generation_span(name="intent", model="claude-sonnet-5", messages=[{"role": "user", "content": "hi"}]) as gen:
            assert gen is generation

    get_client.return_value.start_as_current_observation.assert_called_once_with(
        name="intent", as_type="generation", model="claude-sonnet-5", input=[{"role": "user", "content": "hi"}]
    )

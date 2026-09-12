"""Per-turn LLM/agent tracing via Langfuse Cloud: prompts, token usage, and
per-node graph execution, tied to one trace per start()/respond() call and
grouped into one Langfuse "session" per conversation (thread_id).

Cloud, not self-hosted: FHIR Bud's volume is far inside the free tier, so the
self-hosted stack would be pure ops overhead for no benefit. Ported from
orthopedics-product-agents' backend/observability/langfuse_setup.py, adapted
for this app's shape:

  - One graph (backend/graph.py), not a registry of named workflows, so
    there's no per-workflow tag to thread through.
  - backend/graph.py's intent/reasoning nodes call anthropic.Anthropic
    directly, not a LangChain LLM wrapper -- so langfuse.langchain.
    CallbackHandler alone only captures node-level graph spans (intent/
    reasoning/retrieval/clarification/finalize), not the generation-level
    detail (prompt, token usage) a LangChain wrapper gets for free.
    generation_span() below wraps those two call sites manually to get that
    same detail.
  - No judge/eval-score concept in this app, so there's no score_trace()
    counterpart here.

The Langfuse SDK itself no-ops safely (logs a warning, never raises) when
LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY aren't set -- e.g. in CI, which has
neither -- so nothing here needs to guard against that.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from langfuse import get_client
from langfuse.langchain import CallbackHandler


def configure_langfuse() -> None:
    """Call once at process startup (backend/api.py's lifespan) so the client
    initializes -- and logs a warning if unconfigured -- up front rather than
    silently on the first chat request.
    """
    get_client()


def new_callback_handler(*, user_id: str | None, session_id: str) -> tuple[CallbackHandler, dict]:
    """A fresh per-turn handler plus the LangGraph config fragment (callbacks +
    metadata) that correlates its trace to this user/conversation -- merge the
    returned dict into the graph's own `config`:

        handler, langfuse_config = new_callback_handler(...)
        config = {**config, **langfuse_config}

    `handler.last_trace_id` is populated once the graph run completes -- pass
    it to get_trace_url() afterward. user_id is None for guest conversations
    (see backend/agent.py's persist flag); tagged "guest" so those turns are
    still filterable as a group in Langfuse's UI.
    """
    handler = CallbackHandler()
    config_fragment = {
        "callbacks": [handler],
        "metadata": {
            "langfuse_session_id": session_id,
            "langfuse_user_id": user_id or "guest",
        },
    }
    return handler, config_fragment


@contextmanager
def generation_span(*, name: str, model: str, messages: list[dict]) -> Iterator[Any]:
    """Wraps one raw anthropic.Anthropic().messages.create() call as a
    Langfuse generation observation, nested under whatever LangGraph node
    span new_callback_handler's handler is currently in -- the prompt/token/
    latency detail langfuse.langchain.CallbackHandler only captures for free
    from a LangChain LLM wrapper, which this codebase doesn't use.

    Usage:
        with generation_span(name="intent", model=intent_model, messages=messages) as generation:
            response = client.messages.create(model=intent_model, messages=messages, ...)
            generation.update(
                output=[content_block_to_dict(b) for b in response.content],
                usage_details={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
            )
    """
    client = get_client()
    with client.start_as_current_observation(
        name=name, as_type="generation", model=model, input=messages
    ) as generation:
        yield generation


def get_trace_url(trace_id: str | None) -> str | None:
    """Best-effort only: get_client().get_trace_url() calls the Langfuse API
    synchronously (unlike tracing, which queues and fails silently in the
    background) -- e.g. it raises on bad credentials -- and this is called
    right after a chat turn's answer already exists. Never let it take the
    answer down with it.
    """
    if not trace_id:
        return None
    try:
        return get_client().get_trace_url(trace_id=trace_id)
    except Exception as exc:
        print(f"[langfuse] get_trace_url failed; continuing without it: {exc}")
        return None

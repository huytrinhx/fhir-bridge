"""Multi-turn orchestration: intent gate -> clarify-or-search loop -> synthesis.

A conversation is a FhirBridgeSession: start() with the user's initial use-case
description, then respond() with each answer to a clarifying question, until
the session returns a FinalRecommendation (or OutOfScope, at the start).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import anthropic
import psycopg
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row

from backend.config import Settings, load_settings
from backend.event_stream import CompositeEventLogger
from backend.graph import build_graph, content_block_to_dict
from backend.guardrails import Citation, Recommendation, load_whitelist
from backend.persistence import EventLogger
from backend.retrieval import FhirRetriever

DATA_SAMPLE_MAX_CHARS = 4000


def compose_use_case(
    message: str,
    data_sample: str | None = None,
    data_format: str | None = None,
    terminology_system: str | None = None,
) -> str:
    """Build the actual first-turn text sent to the agent from the structured
    input-panel fields (data sample is truncated context, never parsed)."""
    parts = [message]

    if data_format:
        parts.append(f"Source data format: {data_format}")

    if terminology_system:
        parts.append(f"Desired terminology system for coded elements: {terminology_system}")

    if data_sample:
        sample = data_sample.strip()[:DATA_SAMPLE_MAX_CHARS]
        parts.append(f"Sample data:\n```\n{sample}\n```")

    return "\n\n".join(parts)


def serialize_messages(messages: list[dict]) -> list[dict]:
    """Convert the SDK-shaped message list (typed content blocks mixed with
    plain dicts) into a JSON-safe structure for persistence."""
    serialized = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            serialized_content: object = content
        else:
            serialized_content = [content_block_to_dict(block) for block in content]
        serialized.append({"role": message["role"], "content": serialized_content})
    return serialized


def build_display_transcript(messages: list[dict]) -> list[dict]:
    """Turn the raw message list into a legible sequence for history display --
    text/search/question/answer entries instead of raw SDK content blocks.

    A tool_result block only carries a tool_use_id, not which tool it answers,
    so tool names are tracked from each preceding tool_use block (assistant
    turns always precede the user turn holding their results) rather than
    guessed from the result's shape.
    """
    entries: list[dict] = []
    tool_name_by_id: dict[str, str] = {}

    for message in messages:
        role = message["role"]
        content = message["content"]

        if isinstance(content, str):
            entries.append({"role": role, "kind": "message", "text": content})
            continue

        for block in content:
            block = content_block_to_dict(block)
            block_type = block.get("type")

            if block_type == "text":
                text = block.get("text", "").strip()
                if text:
                    entries.append({"role": role, "kind": "message", "text": text})
            elif block_type == "tool_use":
                tool_name_by_id[block.get("id", "")] = block.get("name", "")
                if block.get("name") == "search_fhir_kb":
                    query = block.get("input", {}).get("query", "")
                    entries.append({"role": role, "kind": "search", "text": f"Searched: {query!r}"})
                elif block.get("name") == "ask_clarifying_question":
                    question = block.get("input", {}).get("question", "")
                    entries.append({"role": role, "kind": "question", "text": question})
            elif block_type == "tool_result":
                source_tool = tool_name_by_id.get(block.get("tool_use_id", ""))
                text = block.get("content", "")
                if source_tool == "ask_clarifying_question" and isinstance(text, str) and text:
                    entries.append({"role": "user", "kind": "answer", "text": text})
                # search_fhir_kb results are the bulky KB dump -- not shown in
                # the display transcript, only used to ground the model.

    return entries


@dataclass(frozen=True)
class OutOfScope:
    reason: str


@dataclass(frozen=True)
class ClarifyingQuestion:
    question: str
    # 2-4 short option labels the model judged the question option-worthy
    # enough to propose, or None for a genuinely open-ended question -- a
    # free-text answer is always valid either way (see respond()).
    options: list[str] | None


@dataclass(frozen=True)
class FinalRecommendation:
    must_have: list[Recommendation]
    potentially_needed: list[Recommendation]
    dropped: list[str]
    redacted: list[str]


TurnOutcome = OutOfScope | ClarifyingQuestion | FinalRecommendation


class FhirBridgeSession:
    """One conversation. Call start() once, then respond() per clarifying answer.

    Backed by a LangGraph graph (backend/graph.py): a clarifying question
    pauses the graph via interrupt(), and respond() resumes it from that same
    point via Command(resume=...), which requires a checkpointer to hold the
    paused state.

    persist=True (authenticated conversations) backs that checkpointer with
    Postgres, on its own connection for the life of this session -- call
    close() when done with it (see backend/api.py's idle-session sweep).
    persist=False (guest conversations) keeps it in memory only: guests get
    zero persistence, full stop, so nothing about a guest session may touch
    Postgres, including graph checkpoints (see agents.md).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        synth_model: str | None = None,
        intent_model: str | None = None,
        *,
        persist: bool = True,
        session_id: str | None = None,
    ):
        self._settings = settings or load_settings()
        # Intent gate model is admin-configured (backend/api.py::_resolve_model_
        # defaults), independent of synth_model -- the scope gate is cheap and
        # reliability there matters more than per-user choice; there's no
        # per-conversation UI for it, unlike synth_model which the user picks
        # from a dropdown.
        self._intent_model = intent_model or self._settings.intent_model
        self._synth_model = synth_model or self._settings.synth_model
        self._client = anthropic.Anthropic(
            base_url=self._settings.kyma_messages_base_url,
            api_key=self._settings.kyma_api_key,
        )
        self._retriever = FhirRetriever(self._settings)
        self._whitelist = load_whitelist(self._settings.whitelist_path)

        self._checkpoint_conn: psycopg.Connection | None = None
        if persist:
            if not self._settings.database_url:
                raise RuntimeError("DATABASE_URL is not set")
            self._checkpoint_conn = psycopg.connect(
                self._settings.database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row
            )
            checkpointer = PostgresSaver(self._checkpoint_conn)
            checkpointer.setup()
        else:
            checkpointer = InMemorySaver()

        # session_id doubles as the graph's checkpoint thread_id and the
        # decision-event log's session_id, so a conversation's event rows
        # correlate with the same id the frontend/API already know it by
        # (see backend/api.py's conversation_id), not a second, invisible id.
        self._thread_id = session_id or str(uuid.uuid4())
        # EventLogger opens its own connections (see backend/persistence.py)
        # rather than sharing self._checkpoint_conn -- settings=None for
        # guests, same persist gate as the checkpointer above. Wrapped in
        # CompositeEventLogger so the live SSE broadcast (backend/api.py,
        # backend/event_stream.py) still fires for guests even though
        # persistence itself doesn't -- the stream isn't persistence.
        self._event_logger = CompositeEventLogger(
            self._thread_id, EventLogger(self._settings if persist else None, self._thread_id)
        )

        self._graph = build_graph(
            client=self._client,
            retriever=self._retriever,
            whitelist=self._whitelist,
            event_logger=self._event_logger,
            intent_model=self._intent_model,
            synth_model=self._synth_model,
            checkpointer=checkpointer,
        )
        self._config = {"configurable": {"thread_id": self._thread_id}, "recursion_limit": 100}

        self._started = False
        self._awaiting_answer = False
        self._messages: list[dict] = []

    def close(self) -> None:
        """Release the Postgres checkpointer connection, if this session ever
        opened one (persist=True). Guest sessions (persist=False) never open
        one, so this is a no-op for them."""
        if self._checkpoint_conn is not None:
            self._checkpoint_conn.close()

    @property
    def messages(self) -> list[dict]:
        """Raw SDK-shaped message list, for persistence (see serialize_messages)."""
        return self._messages

    def start(self, use_case: str) -> TurnOutcome:
        if self._started:
            raise RuntimeError("start() already called on this session")
        self._started = True

        result = self._graph.invoke(
            {"use_case": use_case, "messages": [], "turn_index": 0, "clarification_rounds": 0, "ledger": {}},
            config=self._config,
        )
        return self._extract_outcome(result)

    def respond(self, answer: str) -> TurnOutcome:
        if not self._awaiting_answer:
            raise RuntimeError("no pending clarifying question to respond to")

        result = self._graph.invoke(Command(resume=answer), config=self._config)
        return self._extract_outcome(result)

    def _extract_outcome(self, result: dict) -> TurnOutcome:
        self._messages = result["messages"]

        if result.get("__interrupt__"):
            self._awaiting_answer = True
            ask_block = result["ask_block"]
            return ClarifyingQuestion(question=ask_block["question"], options=ask_block["options"])

        self._awaiting_answer = False
        outcome = result["outcome"]
        if outcome["kind"] == "out_of_scope":
            return OutOfScope(outcome["reason"])

        return FinalRecommendation(
            must_have=[Recommendation(**{**r, "citation": Citation(**r["citation"])}) for r in outcome["must_have"]],
            potentially_needed=[
                Recommendation(**{**r, "citation": Citation(**r["citation"])})
                for r in outcome["potentially_needed"]
            ],
            dropped=outcome["dropped"],
            redacted=outcome["redacted"],
        )

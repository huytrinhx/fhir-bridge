"""Characterization tests for backend/graph.py: the LangGraph replacement for
FhirBridgeSession's hand-rolled tool-use loop. These pin down the same
observable behavior the loop had (see backend/agent.py's prior version in
git history) -- intent gating, search/ask/submit routing, forcing
convergence past the search budget, the max-turns guard, and clarification
as a real pause/resume (interrupt()) rather than an in-process return.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row

from backend.config import load_settings
from backend.graph import (
    MAX_CLARIFICATION_ROUNDS,
    MAX_FORCE_ATTEMPTS,
    MAX_TOOL_LOOP_TURNS,
    build_graph,
)
from backend.guardrails import WhitelistEntry
from backend.retrieval import RetrievedChunk


@dataclass
class FakeBlock:
    """Stands in for an Anthropic SDK content block: real blocks support both
    attribute access (block.type) and .model_dump() -- graph.py's intent
    check uses the former, _content_block_to_dict uses the latter."""

    type: str
    id: str | None = None
    name: str | None = None
    input: dict = field(default_factory=dict)
    text: str = ""

    def model_dump(self) -> dict:
        d: dict = {"type": self.type}
        if self.id is not None:
            d["id"] = self.id
        if self.name is not None:
            d["name"] = self.name
        if self.type == "tool_use":
            d["input"] = self.input
        if self.type == "text":
            d["text"] = self.text
        return d


@dataclass
class FakeResponse:
    content: list[FakeBlock]
    stop_reason: str = "tool_use"


def intent_response(in_scope: bool, reason: str = "test reason") -> FakeResponse:
    return FakeResponse(
        content=[FakeBlock(type="tool_use", id="intent-1", name="classify_intent", input={"in_scope": in_scope, "reason": reason})]
    )


def search_response(query: str, tool_use_id: str = "search-1") -> FakeResponse:
    return FakeResponse(
        content=[FakeBlock(type="tool_use", id=tool_use_id, name="search_fhir_kb", input={"query": query})]
    )


def ask_response(questions: list[str], tool_use_id: str = "ask-1") -> FakeResponse:
    return FakeResponse(
        content=[FakeBlock(type="tool_use", id=tool_use_id, name="ask_clarifying_question", input={"questions": questions})]
    )


def submit_response(must_have: list[dict], potentially_needed: list[dict] | None = None) -> FakeResponse:
    return FakeResponse(
        content=[
            FakeBlock(
                type="tool_use",
                id="submit-1",
                name="submit_recommendation",
                input={"must_have": must_have, "potentially_needed": potentially_needed or []},
            )
        ]
    )


def no_tool_use_response() -> FakeResponse:
    return FakeResponse(content=[FakeBlock(type="text", text="thinking out loud")], stop_reason="end_turn")


class FakeMessages:
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs) -> FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages ran out of scripted responses")
        return self._responses.pop(0)


class FakeAnthropicClient:
    def __init__(self, responses: list[FakeResponse]):
        self.messages = FakeMessages(responses)


class FakeRetriever:
    def __init__(self, chunks_by_query: dict[str, list[RetrievedChunk]] | None = None):
        self._chunks_by_query = chunks_by_query or {}
        self.queries: list[str] = []

    def search(self, query: str, k: int = 8) -> list[RetrievedChunk]:
        self.queries.append(query)
        return self._chunks_by_query.get(query, [])


PATIENT_CHUNK = RetrievedChunk(
    resource_type="Patient",
    text="Demographics and administrative info about an individual.",
    source_url="https://hl7.org/fhir/R4/patient.html",
    spec_version="R4",
    distance=0.05,
)

WHITELIST = {
    "Patient": WhitelistEntry(citation_url="https://hl7.org/fhir/R4/patient.html", spec_version="R4"),
}


def make_graph(client, retriever, checkpointer=None, whitelist=None):
    return build_graph(
        client=client,
        retriever=retriever,
        whitelist=whitelist if whitelist is not None else WHITELIST,
        intent_model="test-intent-model",
        synth_model="test-synth-model",
        checkpointer=checkpointer or InMemorySaver(),
    )


def invoke(graph, payload, thread_id=None):
    config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}, "recursion_limit": 100}
    return graph.invoke(payload, config=config), config


def start_payload(use_case: str) -> dict:
    return {"use_case": use_case, "messages": [], "turn_index": 0, "clarification_rounds": 0, "ledger": {}}


def test_out_of_scope_short_circuits_before_reasoning():
    client = FakeAnthropicClient([intent_response(in_scope=False, reason="not healthcare related")])
    retriever = FakeRetriever()
    graph = make_graph(client, retriever)

    result, _ = invoke(graph, start_payload("what's a good pizza topping"))

    assert result["outcome"] == {"kind": "out_of_scope", "reason": "not healthcare related"}
    assert result["messages"] == []
    assert len(client.messages.calls) == 1  # only the intent call, no reasoning turn


def test_search_then_submit_produces_final_recommendation():
    client = FakeAnthropicClient(
        [
            intent_response(in_scope=True),
            search_response("patient demographics"),
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ]
    )
    retriever = FakeRetriever({"patient demographics": [PATIENT_CHUNK]})
    graph = make_graph(client, retriever)

    result, _ = invoke(graph, start_payload("raw patient registration feed"))

    assert result["outcome"]["kind"] == "final_recommendation"
    assert [r["resource_type"] for r in result["outcome"]["must_have"]] == ["Patient"]
    assert result["outcome"]["dropped"] == []
    assert retriever.queries == ["patient demographics"]
    # first user turn, assistant search, user tool_result, assistant submit
    assert [m["role"] for m in result["messages"]] == ["user", "assistant", "user", "assistant"]


def test_submit_of_uncited_resource_is_dropped():
    client = FakeAnthropicClient(
        [
            intent_response(in_scope=True),
            # Submits Patient without ever searching for it this conversation.
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ]
    )
    graph = make_graph(client, FakeRetriever())

    result, _ = invoke(graph, start_payload("raw patient registration feed"))

    assert result["outcome"]["must_have"] == []
    assert "not retrieved this turn" in result["outcome"]["dropped"][0]


def test_clarifying_question_pauses_and_resumes_via_interrupt():
    client = FakeAnthropicClient(
        [
            intent_response(in_scope=True),
            ask_response(["Do you need device identity tracked?"]),
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ]
    )
    retriever = FakeRetriever({"patient": [PATIENT_CHUNK]})
    graph = make_graph(client, retriever)

    result1, config = invoke(graph, start_payload("raw patient registration feed"))

    assert "__interrupt__" in result1
    assert result1["__interrupt__"][0].value == {"questions": ["Do you need device identity tracked?"]}
    assert result1.get("outcome") is None
    # The assistant's ask turn is already recorded even though we're paused.
    assert [m["role"] for m in result1["messages"]] == ["user", "assistant"]

    result2 = graph.invoke(Command(resume="No device tracking needed."), config)

    assert result2["outcome"]["kind"] == "final_recommendation"
    assert result2["clarification_rounds"] == 1
    # The answer lands as a tool_result attached to the ask_clarifying_question call.
    answer_message = result2["messages"][2]
    assert answer_message["role"] == "user"
    assert answer_message["content"][0]["content"] == "No device tracking needed."
    assert answer_message["content"][0]["tool_use_id"] == "ask-1"


def test_search_co_occurring_with_ask_is_serviced_and_queued_as_pending():
    # A turn that both searches and asks is unusual (tool_choice="any" nudges
    # one call) but the original loop served it defensively -- replicate
    # that: the search still updates the ledger and gets a tool_result, but
    # that tool_result is stashed for respond() to attach alongside the
    # answer, not appended as its own message right away.
    client = FakeAnthropicClient(
        [
            intent_response(in_scope=True),
            FakeResponse(
                content=[
                    FakeBlock(type="tool_use", id="search-1", name="search_fhir_kb", input={"query": "patient"}),
                    FakeBlock(
                        type="tool_use",
                        id="ask-1",
                        name="ask_clarifying_question",
                        input={"questions": ["Need billing ties?"]},
                    ),
                ]
            ),
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ]
    )
    retriever = FakeRetriever({"patient": [PATIENT_CHUNK]})
    graph = make_graph(client, retriever)

    result1, config = invoke(graph, start_payload("raw patient registration feed"))

    assert "__interrupt__" in result1
    assert "Patient" in result1["ledger"]
    # Not yet appended to messages -- only present once resumed.
    assert [m["role"] for m in result1["messages"]] == ["user", "assistant"]

    result2 = graph.invoke(Command(resume="Yes, tie to an encounter."), config)

    answer_message = result2["messages"][2]
    contents = answer_message["content"]
    assert [c["tool_use_id"] for c in contents] == ["search-1", "ask-1"]
    assert result2["outcome"]["kind"] == "final_recommendation"


def test_turn_budget_resets_on_each_resumed_leg():
    # The old loop's turn budget was a fresh local counter on every
    # start()/respond() call, not a whole-conversation total. Five search
    # turns before the ask, then five more after resuming (ten total, more
    # than MAX_TOOL_LOOP_TURNS + MAX_FORCE_ATTEMPTS = 9) must still succeed,
    # because each leg only ever spends 6 of its own budget.
    pre_ask_turns = 5
    post_ask_turns = 5
    assert pre_ask_turns + post_ask_turns > MAX_TOOL_LOOP_TURNS + MAX_FORCE_ATTEMPTS
    assert pre_ask_turns < MAX_TOOL_LOOP_TURNS and post_ask_turns < MAX_TOOL_LOOP_TURNS

    responses = [intent_response(in_scope=True)]
    for i in range(pre_ask_turns):
        responses.append(search_response(f"pre-{i}", tool_use_id=f"pre-{i}"))
    responses.append(ask_response(["Need device tracking?"]))
    for i in range(post_ask_turns):
        responses.append(search_response(f"post-{i}", tool_use_id=f"post-{i}"))
    responses.append(submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]))

    client = FakeAnthropicClient(responses)
    chunks = {f"pre-{i}": [] for i in range(pre_ask_turns)} | {f"post-{i}": [] for i in range(post_ask_turns)}
    chunks["post-0"] = [PATIENT_CHUNK]  # give the submit something to cite
    retriever = FakeRetriever(chunks)
    graph = make_graph(client, retriever)

    result1, config = invoke(graph, start_payload("raw patient registration feed"))
    assert "__interrupt__" in result1

    result2 = graph.invoke(Command(resume="No device tracking needed."), config)

    assert result2["outcome"]["kind"] == "final_recommendation"


def test_no_tool_use_turn_gets_nudged_and_retried():
    client = FakeAnthropicClient(
        [
            intent_response(in_scope=True),
            no_tool_use_response(),
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ]
    )
    graph = make_graph(client, FakeRetriever())

    result, _ = invoke(graph, start_payload("raw patient registration feed"))

    assert result["outcome"]["kind"] == "final_recommendation"
    roles_and_texts = [(m["role"], m["content"]) for m in result["messages"]]
    assert roles_and_texts[2] == (
        "user",
        "You must call exactly one tool this turn: search_fhir_kb, ask_clarifying_question, or submit_recommendation.",
    )


def test_forcing_convergence_drops_search_tool_but_still_services_it():
    responses = [intent_response(in_scope=True)]
    # MAX_TOOL_LOOP_TURNS search turns, then one more search once forcing
    # convergence kicks in (the model ignores the hint, as the loop this
    # replaces explicitly tolerates), then finally submits.
    for i in range(MAX_TOOL_LOOP_TURNS):
        responses.append(search_response(f"query-{i}", tool_use_id=f"search-{i}"))
    responses.append(search_response("late-query", tool_use_id="search-late"))
    responses.append(submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]))

    client = FakeAnthropicClient(responses)
    retriever = FakeRetriever({f"query-{i}": [] for i in range(MAX_TOOL_LOOP_TURNS)} | {"late-query": [PATIENT_CHUNK]})
    graph = make_graph(client, retriever)

    result, _ = invoke(graph, start_payload("raw patient registration feed"))

    assert result["outcome"]["kind"] == "final_recommendation"

    # The call after the search budget is exhausted must not offer SEARCH_TOOL.
    forcing_call = client.messages.calls[MAX_TOOL_LOOP_TURNS + 1]  # +1 for the intent call
    tool_names = {t["name"] for t in forcing_call["tools"]}
    assert "search_fhir_kb" not in tool_names

    # The late search's tool_result still got the "no more searching" nudge appended.
    late_search_result_message = next(
        m
        for m in result["messages"]
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(c.get("tool_use_id") == "search-late" for c in m["content"])
    )
    text = late_search_result_message["content"][0]["content"]
    assert "No more searching" in text


def test_ask_tool_withdrawn_after_max_clarification_rounds():
    responses = [intent_response(in_scope=True)]
    for i in range(MAX_CLARIFICATION_ROUNDS):
        responses.append(ask_response([f"question {i}?"], tool_use_id=f"ask-{i}"))
    responses.append(submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]))

    client = FakeAnthropicClient(responses)
    graph = make_graph(client, FakeRetriever())

    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 100}
    result = graph.invoke(start_payload("raw patient registration feed"), config)
    for _ in range(MAX_CLARIFICATION_ROUNDS):
        assert "__interrupt__" in result
        result = graph.invoke(Command(resume="best judgement"), config)

    assert result["outcome"]["kind"] == "final_recommendation"

    # The very last reasoning call (right before submit) must not have offered ASK_TOOL.
    final_call = client.messages.calls[-1]
    tool_names = {t["name"] for t in final_call["tools"]}
    assert "ask_clarifying_question" not in tool_names


def test_exceeding_max_turns_raises_runtime_error():
    total_turns = MAX_TOOL_LOOP_TURNS + MAX_FORCE_ATTEMPTS
    responses = [intent_response(in_scope=True)]
    for i in range(total_turns):
        responses.append(search_response(f"query-{i}", tool_use_id=f"s{i}"))
    # One extra in case the implementation makes one more call than expected --
    # if it doesn't, FakeMessages simply won't be asked for it.
    responses.append(search_response("unused"))

    client = FakeAnthropicClient(responses)
    retriever = FakeRetriever({f"query-{i}": [] for i in range(total_turns)})
    graph = make_graph(client, retriever)

    with pytest.raises(RuntimeError, match="tool-use loop exceeded max turns"):
        invoke(graph, start_payload("raw patient registration feed"))


def test_postgres_checkpointer_persists_state_after_each_node():
    settings = load_settings()
    if not settings.database_url:
        pytest.skip("DATABASE_URL not configured for local Postgres")

    conn = psycopg.connect(settings.database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    checkpointer = PostgresSaver(conn)
    checkpointer.setup()

    client = FakeAnthropicClient(
        [
            intent_response(in_scope=True),
            ask_response(["Need device tracking?"]),
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ]
    )
    retriever = FakeRetriever({"patient": [PATIENT_CHUNK]})
    graph = make_graph(client, retriever, checkpointer=checkpointer)

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s", (thread_id,))
        before = cur.fetchone()["n"]
    assert before == 0

    graph.invoke(start_payload("raw patient registration feed"), config)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s", (thread_id,))
        after_first = cur.fetchone()["n"]
    assert after_first > 0

    graph.invoke(Command(resume="No device tracking needed."), config)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s", (thread_id,))
        after_second = cur.fetchone()["n"]
    assert after_second > after_first

    conn.close()

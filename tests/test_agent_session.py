"""FhirBridgeSession is a thin wrapper around backend.graph's compiled graph
(see tests/test_graph.py for the loop-behavior characterization tests) --
these cover the wrapper's own contract: start()/respond() guards, and
turning the graph's plain-dict outcome back into OutOfScope/ClarifyingQuestion/
FinalRecommendation.
"""
from __future__ import annotations

import pytest

import backend.agent as agent_module
from backend.agent import ClarifyingQuestion, FhirBridgeSession, FinalRecommendation, OutOfScope
from backend.config import load_settings
from backend.guardrails import WhitelistEntry
from backend.persistence import ensure_schema, get_connection
from tests.test_graph import (
    FakeAnthropicClient,
    FakeRetriever,
    PATIENT_CHUNK,
    ask_response,
    intent_response,
    search_response,
    submit_response,
)

WHITELIST = {
    "Patient": WhitelistEntry(citation_url="https://hl7.org/fhir/R4/patient.html", spec_version="R4"),
}


def make_session(monkeypatch, responses, chunks_by_query=None, persist=True) -> FhirBridgeSession:
    settings = load_settings()
    if persist and not settings.database_url:
        pytest.skip("DATABASE_URL not configured for local Postgres")

    if persist:
        # Mirrors backend/api.py's _resolve_model_defaults, which always
        # ensures the schema exists before any persist=True session is built.
        conn = get_connection(settings)
        try:
            ensure_schema(conn)
        finally:
            conn.close()

    fake_client = FakeAnthropicClient(responses)
    fake_retriever = FakeRetriever(chunks_by_query or {})

    monkeypatch.setattr(agent_module.anthropic, "Anthropic", lambda **kwargs: fake_client)
    monkeypatch.setattr(agent_module, "FhirRetriever", lambda settings: fake_retriever)
    monkeypatch.setattr(agent_module, "load_whitelist", lambda path: WHITELIST)

    return FhirBridgeSession(settings, persist=persist)


def test_start_out_of_scope(monkeypatch):
    session = make_session(monkeypatch, [intent_response(in_scope=False, reason="unrelated")])

    outcome = session.start("what's the best pizza topping")

    assert outcome == OutOfScope("unrelated")
    assert session.messages == []


def test_start_already_called_raises(monkeypatch):
    session = make_session(monkeypatch, [intent_response(in_scope=False, reason="unrelated")])
    session.start("what's the best pizza topping")

    with pytest.raises(RuntimeError, match="start\\(\\) already called"):
        session.start("another message")


def test_respond_without_pending_question_raises(monkeypatch):
    session = make_session(monkeypatch, [intent_response(in_scope=False, reason="unrelated")])
    session.start("what's the best pizza topping")

    with pytest.raises(RuntimeError, match="no pending clarifying question"):
        session.respond("some answer")


def test_full_round_trip_via_session(monkeypatch):
    session = make_session(
        monkeypatch,
        [
            intent_response(in_scope=True),
            search_response("patient demographics"),
            ask_response(["Need device tracking?"]),
            submit_response([{"resource_type": "Patient", "rationale": "core identity record"}]),
        ],
        chunks_by_query={"patient demographics": [PATIENT_CHUNK]},
    )

    outcome = session.start("raw patient registration feed")
    assert isinstance(outcome, ClarifyingQuestion)
    assert len(outcome.questions) == 1
    assert outcome.questions[0].question == "Need device tracking?"
    assert outcome.questions[0].options is None

    final = session.respond("No device tracking needed.")
    assert isinstance(final, FinalRecommendation)
    assert [r.resource_type for r in final.must_have] == ["Patient"]
    assert final.must_have[0].citation.url == "https://hl7.org/fhir/R4/patient.html"
    assert final.dropped == []

    # messages property reflects the full transcript for persistence: initial
    # use case, search turn + its result, ask turn + its answer, submit turn.
    assert [m["role"] for m in session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_guest_session_never_opens_a_postgres_connection(monkeypatch):
    # agents.md: "Guests get zero persistence, full stop. Nothing about a
    # guest session touches Postgres or logs." That includes the graph's own
    # checkpointer, not just the conversations table -- persist=False must
    # never dial Postgres at all, not merely skip writing to it.
    def fail_if_called(*args, **kwargs):
        raise AssertionError("guest session must never open a Postgres connection")

    monkeypatch.setattr(agent_module.psycopg, "connect", fail_if_called)

    session = make_session(
        monkeypatch,
        [intent_response(in_scope=False, reason="unrelated")],
        persist=False,
    )

    assert session._checkpoint_conn is None
    outcome = session.start("what's the best pizza topping")
    assert isinstance(outcome, OutOfScope)

    session.close()  # no-op without a connection -- must not raise


def test_persist_true_opens_and_close_releases_the_checkpoint_connection(monkeypatch):
    session = make_session(monkeypatch, [intent_response(in_scope=False, reason="unrelated")], persist=True)

    assert session._checkpoint_conn is not None
    assert session._checkpoint_conn.closed == 0

    session.close()

    assert session._checkpoint_conn.closed != 0

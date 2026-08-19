"""Multi-turn orchestration: intent gate -> clarify-or-search loop -> synthesis.

A conversation is a FhirBridgeSession: start() with the user's initial use-case
description, then respond() with each answer to a clarifying question, until
the session returns a FinalRecommendation (or OutOfScope, at the start).
"""
from __future__ import annotations

from dataclasses import dataclass

import anthropic

from backend.config import Settings, load_settings
from backend.guardrails import (
    CitationLedger,
    Recommendation,
    load_whitelist,
    validate_recommendations,
)
from backend.retrieval import FhirRetriever

MAX_TOOL_LOOP_TURNS = 6
MAX_FORCE_ATTEMPTS = 3
MAX_CLARIFICATION_ROUNDS = 2

SEARCH_TOOL = {
    "name": "search_fhir_kb",
    "description": (
        "Search the FHIR R4 knowledge base for candidate resource types relevant to a "
        "described use case. Always use this before naming any FHIR resource -- never "
        "name a resource from memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "natural-language search query"}},
        "required": ["query"],
    },
}

ASK_TOOL = {
    "name": "ask_clarifying_question",
    "description": (
        "Ask the user 1-3 targeted follow-up questions when there is genuine ambiguity "
        "that would change which resources are must-have vs potentially-needed (e.g. "
        "whether device identity needs tracking, whether billing ties to an encounter). "
        "Do not ask about anything that wouldn't change the recommendation. Call this "
        "alone, not combined with submit_recommendation in the same turn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
            }
        },
        "required": ["questions"],
    },
}

SUBMIT_TOOL = {
    "name": "submit_recommendation",
    "description": "Submit the final categorized list of FHIR resources for this use case.",
    "input_schema": {
        "type": "object",
        "properties": {
            "must_have": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "resource_type": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["resource_type", "rationale"],
                },
            },
            "potentially_needed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "resource_type": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["resource_type", "rationale"],
                },
            },
        },
        "required": ["must_have", "potentially_needed"],
    },
}

INTENT_TOOL = {
    "name": "classify_intent",
    "description": (
        "Classify whether the user's message is a healthcare data-interoperability / "
        "FHIR data-modeling question."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "in_scope": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": ["in_scope", "reason"],
    },
}

SYNTH_SYSTEM_PROMPT = f"""You help software engineers figure out which FHIR R4 resource \
types they need to model a real-world healthcare data source they describe, through a \
short back-and-forth conversation -- not a one-shot answer.

Rules:
- You must call search_fhir_kb before naming any FHIR resource. Never name a resource \
you have not just retrieved.
- Only use resource type names exactly as returned by search_fhir_kb.
- Don't assume any resource is "obvious enough" to skip searching for -- Patient in \
particular belongs in almost every recommendation, but still requires its own \
search_fhir_kb call like anything else before you can name it.
- Never state or invent a specific terminology code (LOINC/SNOMED/etc). If codes matter, \
say so in the rationale but do not output a code value.
- If the user mentions a source data format (CSV, HL7v2, JSON, XML, etc.), use it to \
sharpen your clarifying questions -- e.g. for HL7v2, which message type/segments are \
involved; for CSV, whether one row maps to one resource instance or needs splitting \
across several. Never assert a specific format-to-FHIR field mapping from memory (e.g. \
"HL7v2 OBX maps to Observation.value") -- you have no retrieved source for that claim, so \
it's exactly the kind of invented detail these rules exist to prevent. Let format context \
shape which questions you ask and which resources you search for, not what you assert.
- If the user states a desired terminology system (LOINC, SNOMED CT, ICD-10-CM, RxNorm, \
etc.), reference it by name in your rationale where a coded element is relevant. Still \
never state an actual code from that system -- there is no ingested terminology corpus to \
verify one against, so a specific code would be just as invented as a fabricated resource \
name.
- If there is genuine ambiguity that would change the must-have/potentially-needed split, \
call ask_clarifying_question with 1-3 targeted questions instead of guessing. Only do \
this up to {MAX_CLARIFICATION_ROUNDS} rounds total -- after that the tool will no longer \
be available and you must submit your best recommendation with what you have, noting \
remaining uncertainty in the rationale.
- Categorize each resource as must_have (structurally required to represent the core \
data described) or potentially_needed (commonly paired, situational).
- Prefer to converge quickly: 2-4 searches is usually enough before you should either \
ask a clarifying question or submit. Don't keep searching indefinitely.
- When you have enough information, call submit_recommendation exactly once with your \
final answer.
"""

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


def _content_block_to_dict(block: object) -> dict:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    raise TypeError(f"cannot serialize content block of type {type(block)!r}")


def serialize_messages(messages: list[dict]) -> list[dict]:
    """Convert the SDK-shaped message list (typed content blocks mixed with
    plain dicts) into a JSON-safe structure for persistence."""
    serialized = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            serialized_content: object = content
        else:
            serialized_content = [_content_block_to_dict(block) for block in content]
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
            block = _content_block_to_dict(block)
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
                    questions = block.get("input", {}).get("questions", [])
                    entries.append({"role": role, "kind": "question", "text": " / ".join(questions)})
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
    questions: list[str]


@dataclass(frozen=True)
class FinalRecommendation:
    must_have: list[Recommendation]
    potentially_needed: list[Recommendation]
    dropped: list[str]
    redacted: list[str]


TurnOutcome = OutOfScope | ClarifyingQuestion | FinalRecommendation


class FhirBridgeSession:
    """One conversation. Call start() once, then respond() per clarifying answer."""

    def __init__(
        self,
        settings: Settings | None = None,
        synth_model: str | None = None,
        intent_model: str | None = None,
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
        self._ledger = CitationLedger()
        self._messages: list[dict] = []
        self._clarification_rounds = 0
        self._pending_question_id: str | None = None
        self._pending_tool_results: list[dict] = []

    @property
    def messages(self) -> list[dict]:
        """Raw SDK-shaped message list, for persistence (see serialize_messages)."""
        return self._messages

    def _check_intent(self, use_case: str) -> tuple[bool, str]:
        msg = self._client.messages.create(
            model=self._intent_model,
            max_tokens=300,
            tools=[INTENT_TOOL],
            tool_choice={"type": "tool", "name": "classify_intent"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "This assistant helps engineers figure out which FHIR resources to "
                        "use for real-world data sources they describe. Inputs are often "
                        "short fragments, not full questions -- e.g. 'raw data from vital "
                        "monitors' or 'supply orders and charges' are typical, valid inputs. "
                        "Classify in_scope=true if the message describes, even briefly, a "
                        "real-world data source, business process, or record type that "
                        "plausibly needs representing as healthcare/clinical/administrative "
                        "data. Classify in_scope=false only if it is unrelated to healthcare "
                        "data entirely (general chit-chat, unrelated coding questions, "
                        f"off-topic requests). Message: {use_case!r}"
                    ),
                }
            ],
        )
        for block in msg.content:
            if block.type == "tool_use" and block.name == "classify_intent":
                return bool(block.input["in_scope"]), str(block.input["reason"])
        raise RuntimeError("intent gate did not return a classification")

    def start(self, use_case: str) -> TurnOutcome:
        if self._messages:
            raise RuntimeError("start() already called on this session")

        in_scope, reason = self._check_intent(use_case)
        if not in_scope:
            return OutOfScope(reason)

        self._messages.append({"role": "user", "content": use_case})
        return self._run_loop()

    def respond(self, answer: str) -> TurnOutcome:
        if self._pending_question_id is None:
            raise RuntimeError("no pending clarifying question to respond to")

        tool_results = list(self._pending_tool_results)
        tool_results.append(
            {"type": "tool_result", "tool_use_id": self._pending_question_id, "content": answer}
        )
        self._messages.append({"role": "user", "content": tool_results})
        self._pending_question_id = None
        self._pending_tool_results = []
        return self._run_loop()

    def _run_loop(self) -> TurnOutcome:
        ask_available = self._clarification_rounds < MAX_CLARIFICATION_ROUNDS
        total_turns = MAX_TOOL_LOOP_TURNS + MAX_FORCE_ATTEMPTS

        for turn_index in range(total_turns):
            # Past the normal budget, stop offering search_fhir_kb so the model
            # can't keep exploring instead of converging. This is a hint, not a
            # guarantee -- Kyma doesn't strictly enforce the tools allow-list,
            # so the model can still call it anyway (see below, every tool_use
            # block always gets answered regardless of what was "offered").
            # tool_choice="any" forces some tool call every turn.
            forcing_convergence = turn_index >= MAX_TOOL_LOOP_TURNS
            tools = [SUBMIT_TOOL]
            if not forcing_convergence:
                tools.append(SEARCH_TOOL)
            if ask_available:
                tools.append(ASK_TOOL)

            response = self._client.messages.create(
                model=self._synth_model,
                max_tokens=1500,
                system=SYNTH_SYSTEM_PROMPT,
                tools=tools,
                tool_choice={"type": "any"},
                messages=self._messages,
            )
            self._messages.append({"role": "assistant", "content": response.content})

            if not any(block.type == "tool_use" for block in response.content):
                # tool_choice="any" isn't a hard guarantee on every backend --
                # nudge and retry rather than silently wasting the turn on an
                # empty tool_results message (which used to happen here).
                self._messages.append(
                    {
                        "role": "user",
                        "content": (
                            "You must call exactly one tool this turn: search_fhir_kb, "
                            "ask_clarifying_question, or submit_recommendation."
                        ),
                    }
                )
                continue

            tool_results = []
            submitted = None
            question_id = None
            questions: list[str] = []

            for block in response.content:
                if block.type != "tool_use":
                    continue
                if block.name == "search_fhir_kb":
                    # Always service this and attach a tool_result, even during
                    # forcing_convergence when it wasn't offered -- an
                    # unanswered tool_use block leaves the conversation
                    # unrecoverable on the next call. Add explicit pressure
                    # instead of silently rewarding the ignored hint.
                    chunks = self._retriever.search(block.input["query"])
                    self._ledger.record(chunks)
                    result_text = (
                        "\n".join(f"- {c.resource_type}: {c.text} ({c.source_url})" for c in chunks)
                        or "No matches found."
                    )
                    if forcing_convergence:
                        result_text += (
                            "\n\n(No more searching -- call ask_clarifying_question or "
                            "submit_recommendation now.)"
                        )
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": result_text}
                    )
                elif block.name == "ask_clarifying_question":
                    question_id = block.id
                    questions = list(block.input["questions"])
                elif block.name == "submit_recommendation":
                    submitted = block.input

            if submitted is not None:
                must_have, dropped_must, redacted_must = validate_recommendations(
                    submitted.get("must_have", []), "must_have", self._whitelist, self._ledger
                )
                potentially_needed, dropped_potential, redacted_potential = validate_recommendations(
                    submitted.get("potentially_needed", []),
                    "potentially_needed",
                    self._whitelist,
                    self._ledger,
                )
                return FinalRecommendation(
                    must_have=must_have,
                    potentially_needed=potentially_needed,
                    dropped=dropped_must + dropped_potential,
                    redacted=redacted_must + redacted_potential,
                )

            if question_id is not None:
                self._clarification_rounds += 1
                self._pending_question_id = question_id
                self._pending_tool_results = tool_results
                return ClarifyingQuestion(questions=questions)

            self._messages.append({"role": "user", "content": tool_results})

        raise RuntimeError(
            "tool-use loop exceeded max turns without a submitted recommendation "
            f"(last stop_reason={response.stop_reason!r})"
        )

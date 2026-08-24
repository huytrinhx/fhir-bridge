"""LangGraph implementation of the tool-use loop behind FhirBridgeSession.

Four conceptual nodes, matching the shape of the hand-rolled loop this
replaces (see backend/agent.py's docstring for the conversation-level
contract):
  - intent: the one-shot scope gate (classify_intent).
  - reasoning: the per-turn model call that decides to search, ask, submit,
    or (if it calls no tool) get nudged to try again.
  - retrieval: services every search_fhir_kb call from the latest reasoning
    turn against the FHIR KB, updating the citation ledger.
  - clarification: surfaces ask_clarifying_question's whole batch of
    questions (plus any proposed per-question options) and pauses the graph
    (via interrupt()) until respond() supplies a single combined answer
    covering the batch -- composed client-side from whatever mix of
    selected options and free text the user gave each question.

State only ever holds JSON-plain data (dicts/lists/strings/ints), never SDK
objects or the CitationLedger/FhirRetriever/Anthropic client instances --
this is what gets written to the Postgres checkpointer after every node, so
it has to stay picklable-boring and inspectable without importing this app's
classes back into a checkpoint reader.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Protocol, TypedDict

import anthropic
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import interrupt

from backend.guardrails import CitationLedger, WhitelistEntry, validate_recommendations
from backend.retrieval import FhirRetriever, RetrievedChunk

MAX_TOOL_LOOP_TURNS = 6
MAX_FORCE_ATTEMPTS = 3
# Not a UX target -- the model is meant to converge well before this. It's a
# safety ceiling against a runaway loop (and its API cost), now that nothing
# else caps how many clarification round-trips a conversation can have.
MAX_CLARIFICATION_ROUNDS = 20
TOTAL_TURNS = MAX_TOOL_LOOP_TURNS + MAX_FORCE_ATTEMPTS

NO_TOOL_USE_NUDGE = (
    "You must call exactly one tool this turn: search_fhir_kb, "
    "ask_clarifying_question, or submit_recommendation."
)

SEARCH_TOOL = {
    "name": "search_fhir_kb",
    "description": (
        "Search the FHIR R4 knowledge base for candidate resource types relevant to a "
        "described use case. Always use this before naming any FHIR resource -- never "
        "name a resource from memory."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "natural-language search query",
                "minLength": 1,
            }
        },
        "required": ["query"],
    },
}

ASK_TOOL = {
    "name": "ask_clarifying_question",
    "description": (
        "Ask the user one or more targeted follow-up questions, batched into a single "
        "call, when there is genuine ambiguity that would change which resources are "
        "must-have vs potentially-needed (e.g. whether device identity needs tracking, "
        "whether billing ties to an encounter). Batch every question you currently have "
        "into this one call rather than trickling them out across separate turns -- the "
        "user answers the whole batch at once, one question at a time with the ability to "
        "go back and revise earlier answers before submitting. For each question, if it "
        "has a small, sensible set of discrete answers, propose 2-4 short option labels "
        "the user can pick from -- but only when they'd genuinely cover the likely "
        "answers; leave options out entirely for a genuinely open-ended question rather "
        "than inventing artificial choices. The user can always type a free-text answer "
        "instead of picking an option, for any question in the batch. Do not ask about "
        "anything that wouldn't change the recommendation. Call this alone, not combined "
        "with submit_recommendation in the same turn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "minLength": 1},
                        "options": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 2,
                            "maxItems": 4,
                        },
                    },
                    "required": ["question"],
                },
            },
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
call ask_clarifying_question instead of guessing. Batch every question you currently have \
into that one call rather than asking one, waiting, then asking another -- the user answers \
the whole batch together. If a question has a small set of sensible discrete answers, \
include 2-4 short options; leave options out for a genuinely open-ended question rather \
than inventing artificial choices. There is no small target round count to hit -- ask as \
many rounds as the use case genuinely needs -- but there is a hard safety ceiling of \
{MAX_CLARIFICATION_ROUNDS} rounds total, after which the tool will no longer be available \
and you must submit your best recommendation with what you have, noting remaining \
uncertainty in the rationale. Well before that ceiling, converge once you have enough to \
make a defensible recommendation -- don't pad a batch with questions that wouldn't change \
it.
- Categorize each resource as must_have (structurally required to represent the core \
data described) or potentially_needed (commonly paired, situational).
- Prefer to converge quickly: 2-4 searches is usually enough before you should either \
ask a clarifying question or submit. Don't keep searching indefinitely.
- When you have enough information, call submit_recommendation exactly once with your \
final answer.
"""


class EventLogger(Protocol):
    """Structural type for backend/persistence.py's EventLogger -- kept as a
    Protocol here (rather than importing that class) so this module stays
    decoupled from psycopg, matching the rest of its dependency shape
    (client/retriever/whitelist/checkpointer are all handed in already
    constructed, never built from scratch here)."""

    def log(self, node_name: str, event_type: str, *, input_data: Any = None, output_data: Any = None) -> None: ...


class GraphState(TypedDict, total=False):
    use_case: str
    messages: list[dict]
    turn_index: int
    clarification_rounds: int
    ledger: dict[str, dict]
    forcing_convergence: bool
    no_tool_use: bool
    search_blocks: list[dict]
    ask_block: dict | None
    submit_input: dict | None
    last_stop_reason: str | None
    pending_tool_results: list[dict]
    outcome: dict | None


def content_block_to_dict(block: object) -> dict:
    """Normalize an Anthropic SDK content block (or an already-plain dict) to
    a plain dict -- shared with backend/agent.py's serialize_messages and
    build_display_transcript, which operate on the same message shape this
    graph produces."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump()
    raise TypeError(f"cannot serialize content block of type {type(block)!r}")


def _check_intent(client: anthropic.Anthropic, intent_model: str, use_case: str) -> tuple[bool, str]:
    msg = client.messages.create(
        model=intent_model,
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


def _parse_tool_uses(content: list[dict]) -> tuple[list[dict], dict | None, dict | None, bool]:
    """(search_blocks, ask_block, submit_input, had_any_tool_use)."""
    search_blocks: list[dict] = []
    ask_block: dict | None = None
    submit_input: dict | None = None
    had_any = False

    for block in content:
        if block.get("type") != "tool_use":
            continue
        had_any = True
        if block["name"] == "search_fhir_kb":
            search_blocks.append({"id": block["id"], "query": block.get("input", {}).get("query", "")})
        elif block["name"] == "ask_clarifying_question":
            questions = [
                {
                    "question": q["question"],
                    "options": list(q["options"]) if q.get("options") else None,
                }
                for q in block["input"]["questions"]
            ]
            ask_block = {"id": block["id"], "questions": questions}
        elif block["name"] == "submit_recommendation":
            submit_input = block["input"]

    return search_blocks, ask_block, submit_input, had_any


def build_graph(
    *,
    client: anthropic.Anthropic,
    retriever: FhirRetriever,
    whitelist: dict[str, WhitelistEntry],
    event_logger: EventLogger,
    intent_model: str,
    synth_model: str,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    def intent_node(state: GraphState) -> dict:
        event_logger.log("intent", "start", input_data={"use_case": state["use_case"]})
        in_scope, reason = _check_intent(client, intent_model, state["use_case"])
        event_logger.log("intent", "finish", output_data={"in_scope": in_scope, "reason": reason})
        if not in_scope:
            return {"outcome": {"kind": "out_of_scope", "reason": reason}}
        return {"messages": state["messages"] + [{"role": "user", "content": state["use_case"]}]}

    def route_after_intent(state: GraphState) -> str:
        return END if state.get("outcome") is not None else "reasoning"

    def reasoning_node(state: GraphState) -> dict:
        turn_index = state.get("turn_index", 0)
        if turn_index >= TOTAL_TURNS:
            raise RuntimeError(
                "tool-use loop exceeded max turns without a submitted recommendation "
                f"(last stop_reason={state.get('last_stop_reason')!r})"
            )

        clarification_rounds = state.get("clarification_rounds", 0)
        ask_available = clarification_rounds < MAX_CLARIFICATION_ROUNDS
        forcing_convergence = turn_index >= MAX_TOOL_LOOP_TURNS

        tools = [SUBMIT_TOOL]
        if not forcing_convergence:
            tools.append(SEARCH_TOOL)
        if ask_available:
            tools.append(ASK_TOOL)

        event_logger.log(
            "reasoning",
            "start",
            input_data={
                "turn_index": turn_index,
                "clarification_rounds": clarification_rounds,
                "forcing_convergence": forcing_convergence,
                "available_tools": [t["name"] for t in tools],
            },
        )

        response = client.messages.create(
            model=synth_model,
            max_tokens=1500,
            system=SYNTH_SYSTEM_PROMPT,
            tools=tools,
            tool_choice={"type": "any"},
            messages=state["messages"],
        )
        content = [content_block_to_dict(b) for b in response.content]
        messages = state["messages"] + [{"role": "assistant", "content": content}]

        search_blocks, ask_block, submit_input, had_any_tool_use = _parse_tool_uses(content)
        no_tool_use = not had_any_tool_use
        if no_tool_use:
            messages = messages + [{"role": "user", "content": NO_TOOL_USE_NUDGE}]

        event_logger.log(
            "reasoning",
            "finish",
            output_data={
                "stop_reason": response.stop_reason,
                "no_tool_use": no_tool_use,
                "search_queries": [b["query"] for b in search_blocks],
                "asked_questions": ask_block["questions"] if ask_block else None,
                "submitted": submit_input is not None,
            },
        )

        return {
            "messages": messages,
            "turn_index": turn_index + 1,
            "forcing_convergence": forcing_convergence,
            "no_tool_use": no_tool_use,
            "search_blocks": search_blocks,
            "ask_block": ask_block,
            "submit_input": submit_input,
            "last_stop_reason": response.stop_reason,
        }

    def route_after_reasoning(state: GraphState) -> str:
        if state["no_tool_use"]:
            return "reasoning"
        if state["search_blocks"]:
            return "retrieval"
        if state["submit_input"] is not None:
            return "finalize"
        if state["ask_block"] is not None:
            return "clarification"
        return "reasoning"  # defensive: tool_choice="any" didn't yield a known tool

    def retrieval_node(state: GraphState) -> dict:
        ledger_state = dict(state.get("ledger", {}))
        tool_results: list[dict] = []

        event_logger.log(
            "retrieval", "start", input_data={"queries": [b["query"] for b in state["search_blocks"]]}
        )

        for block in state["search_blocks"]:
            query = block["query"].strip()
            if not query:
                result_text = (
                    "Empty query -- call search_fhir_kb again with a non-empty, "
                    "descriptive search query."
                )
            else:
                chunks = retriever.search(query)
                for chunk in chunks:
                    ledger_state.setdefault(
                        chunk.resource_type,
                        {
                            "text": chunk.text,
                            "source_url": chunk.source_url,
                            "spec_version": chunk.spec_version,
                        },
                    )
                result_text = (
                    "\n".join(f"- {c.resource_type}: {c.text} ({c.source_url})" for c in chunks)
                    or "No matches found."
                )
            if state["forcing_convergence"]:
                result_text += (
                    "\n\n(No more searching -- call ask_clarifying_question or "
                    "submit_recommendation now.)"
                )
            event_logger.log(
                "retrieval",
                "search_fhir_kb",
                input_data={"query": block["query"]},
                output_data={"result_text": result_text},
            )
            tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result_text})

        updates: dict[str, Any] = {"ledger": ledger_state}
        finish_output = {"resource_types_seen": sorted(ledger_state.keys())}

        # A submit or ask co-occurring with search in the same turn takes
        # priority, exactly like the loop this replaces: a submit discards
        # these tool_results (the ledger update they produced still counts,
        # since it's what makes the same-turn citation valid); an ask stashes
        # them to be combined with the eventual answer in clarification_node.
        if state["submit_input"] is not None:
            event_logger.log("retrieval", "finish", output_data=finish_output)
            return updates
        if state["ask_block"] is not None:
            updates["pending_tool_results"] = tool_results
            event_logger.log("retrieval", "finish", output_data=finish_output)
            return updates

        updates["messages"] = state["messages"] + [{"role": "user", "content": tool_results}]
        event_logger.log("retrieval", "finish", output_data=finish_output)
        return updates

    def route_after_retrieval(state: GraphState) -> str:
        if state["submit_input"] is not None:
            return "finalize"
        if state["ask_block"] is not None:
            return "clarification"
        return "reasoning"

    def clarification_node(state: GraphState) -> dict:
        ask_block = state["ask_block"]
        assert ask_block is not None
        # No "start" log here: interrupt() re-executes this node from the top
        # on every resume, so anything logged before it would double-write on
        # each round-trip. One "finish" entry, written only after interrupt()
        # actually resolves with an answer, is the safe rollup point.
        answer = interrupt({"questions": ask_block["questions"]})
        event_logger.log(
            "clarification",
            "finish",
            input_data={"questions": ask_block["questions"]},
            output_data={"answer": answer},
        )

        tool_results = list(state.get("pending_tool_results", []))
        tool_results.append({"type": "tool_result", "tool_use_id": ask_block["id"], "content": answer})

        return {
            "messages": state["messages"] + [{"role": "user", "content": tool_results}],
            "clarification_rounds": state.get("clarification_rounds", 0) + 1,
            "pending_tool_results": [],
            "ask_block": None,
            # The old loop's turn budget was a local `for` counter, fresh on
            # every start()/respond() call -- a resumed conversation leg gets
            # its own MAX_TOOL_LOOP_TURNS + MAX_FORCE_ATTEMPTS budget, not a
            # share of one that accumulates across the whole conversation.
            "turn_index": 0,
        }

    def finalize_node(state: GraphState) -> dict:
        submitted = state["submit_input"] or {}
        ledger = CitationLedger()
        for resource_type, data in state.get("ledger", {}).items():
            ledger.record(
                [
                    RetrievedChunk(
                        resource_type=resource_type,
                        text=data["text"],
                        source_url=data["source_url"],
                        spec_version=data["spec_version"],
                        distance=0.0,
                    )
                ]
            )

        must_have, dropped_must, redacted_must = validate_recommendations(
            submitted.get("must_have", []), "must_have", whitelist, ledger
        )
        potentially_needed, dropped_potential, redacted_potential = validate_recommendations(
            submitted.get("potentially_needed", []), "potentially_needed", whitelist, ledger
        )

        return {
            "outcome": {
                "kind": "final_recommendation",
                # Plain dicts, not Recommendation/Citation instances -- state
                # must stay checkpoint-plain (see module docstring).
                "must_have": [asdict(r) for r in must_have],
                "potentially_needed": [asdict(r) for r in potentially_needed],
                "dropped": dropped_must + dropped_potential,
                "redacted": redacted_must + redacted_potential,
            }
        }

    builder = StateGraph(GraphState)
    builder.add_node("intent", intent_node)
    builder.add_node("reasoning", reasoning_node)
    builder.add_node("retrieval", retrieval_node)
    builder.add_node("clarification", clarification_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "intent")
    builder.add_conditional_edges("intent", route_after_intent, ["reasoning", END])
    builder.add_conditional_edges(
        "reasoning", route_after_reasoning, ["reasoning", "retrieval", "finalize", "clarification"]
    )
    builder.add_conditional_edges(
        "retrieval", route_after_retrieval, ["reasoning", "finalize", "clarification"]
    )
    builder.add_edge("clarification", "reasoning")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)

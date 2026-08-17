"""Deterministic post-generation guardrails: whitelist + citation enforcement.

The model may only ever emit a resource type that (a) is a real FHIR R4
resource per the ingested whitelist, and (b) was actually surfaced by a
search_fhir_kb call earlier in this same turn -- so every recommendation is
traceable to a specific retrieval result, not the model's own memory. The
citation itself must also point at a trusted FHIR host (hl7.org/fhir/): once
the KB has multiple legitimate sources per resource type (core spec, US Core,
and later IGs), there's no longer one canonical URL to compare a chunk
against, so this checks the citation is genuinely from HL7 rather than
requiring it to match one specific static URL. Rationale text is scanned for
invented terminology codes as a second, independent layer under the "never
state a code" prompt rule.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backend.code_guard import sanitize_rationale
from backend.retrieval import RetrievedChunk


@dataclass(frozen=True)
class Citation:
    title: str
    url: str
    spec_version: str


@dataclass(frozen=True)
class Recommendation:
    resource_type: str
    category: str  # "must_have" | "potentially_needed"
    rationale: str
    citation: Citation


@dataclass(frozen=True)
class WhitelistEntry:
    citation_url: str
    spec_version: str


# Every citation must resolve under this host, whichever ingested source it
# came from (core spec, US Core, or a later IG) -- all official HL7-published
# FHIR content lives under this prefix.
TRUSTED_CITATION_PREFIX = "https://hl7.org/fhir/"


def load_whitelist(path: Path) -> dict[str, WhitelistEntry]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: WhitelistEntry(citation_url=meta["citation_url"], spec_version=meta["spec_version"])
        for name, meta in payload["resources"].items()
    }


class CitationLedger:
    """Accumulates every resource actually surfaced by search_fhir_kb this turn."""

    def __init__(self) -> None:
        self._chunks: dict[str, RetrievedChunk] = {}

    def record(self, chunks: list[RetrievedChunk]) -> None:
        for chunk in chunks:
            self._chunks.setdefault(chunk.resource_type, chunk)

    def get(self, resource_type: str) -> RetrievedChunk | None:
        return self._chunks.get(resource_type)


def validate_recommendations(
    raw_items: list[dict],
    category: str,
    whitelist: dict[str, WhitelistEntry],
    ledger: CitationLedger,
) -> tuple[list[Recommendation], list[str], list[str]]:
    """Returns (accepted, dropped_reasons, redacted_notes). Never trusts raw_items alone."""
    accepted: list[Recommendation] = []
    dropped: list[str] = []
    redacted_notes: list[str] = []

    # The whole field can itself be malformed (e.g. a bare string instead of
    # a list) -- strings are iterable, so looping over one character-by-
    # character would otherwise flood `dropped` with one entry per character.
    if not isinstance(raw_items, list):
        return [], [f"{category!r}: malformed, expected a list of items"], []

    for item in raw_items:
        # The tool's input_schema is a request to the model, not a guarantee --
        # it can still send a malformed item (e.g. a bare string instead of an
        # object). Degrade to a drop, don't crash the whole recommendation.
        if not isinstance(item, dict):
            dropped.append(f"{item!r}: malformed recommendation item, expected an object")
            continue

        resource_type = item.get("resource_type", "")
        rationale = item.get("rationale", "")

        if resource_type not in whitelist:
            dropped.append(f"{resource_type!r}: not a valid FHIR R4 resource type")
            continue

        chunk = ledger.get(resource_type)
        if chunk is None:
            dropped.append(f"{resource_type!r}: not retrieved this turn, no citation available")
            continue

        if not chunk.source_url.startswith(TRUSTED_CITATION_PREFIX):
            dropped.append(
                f"{resource_type!r}: citation is not from a trusted FHIR source, dropped for safety"
            )
            continue

        clean_rationale, was_redacted = sanitize_rationale(rationale)
        if was_redacted:
            redacted_notes.append(f"{resource_type!r}: possible terminology code removed from rationale")

        accepted.append(
            Recommendation(
                resource_type=resource_type,
                category=category,
                rationale=clean_rationale,
                citation=Citation(
                    title=resource_type, url=chunk.source_url, spec_version=chunk.spec_version
                ),
            )
        )

    return accepted, dropped, redacted_notes

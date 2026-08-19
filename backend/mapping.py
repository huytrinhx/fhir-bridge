"""Deterministic suggested-payload skeleton + HL7v2 segment-mapping
generation -- no LLM call anywhere in this module. The whole point of this
feature is to avoid the exact hallucination risk backend/agent.py's system
prompt otherwise guards against for format-to-FHIR field claims, so payload
shape comes from fhir_resource_elements (ingested from the real FHIR R4
StructureDefinitions) and segment mappings come from
fhir_v2_segment_mappings (ingested from HL7's official v2-to-FHIR IG) --
both plain exact-match Postgres tables, see ingestion/pipeline/structural_store.py.

Reads its own SQL directly rather than importing ingestion.* -- same
read/write separation as backend/guardrails.py::load_whitelist
re-implementing its own JSON load instead of importing
ingestion.pipeline.whitelist. Callers pass a connection opened with
row_factory=dict_row (e.g. backend/persistence.py::get_connection), which
this module's SELECTs rely on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg

from backend.hl7_parser import parse_message

# v2-to-FHIR ConceptMap target paths mark which repetition of a repeating
# element they mean, e.g. "identifier[1]", "name[1]" -- confirmed against
# real ingested rows for PID-to-Patient. Stripped before matching against
# fhir_resource_elements, which is keyed by bare field name.
_INDEX_SUFFIX_RE = re.compile(r"\[\d+\]$")

# FHIR primitive types only -- complex types (HumanName, Identifier,
# CodeableConcept, Address, Reference, ...) never get a raw HL7v2 value
# folded directly into the skeleton, since this app deliberately never
# parses HL7v2 datatype sub-components (XPN.1, CX.1, ...). The mapping is
# still surfaced in mapped_fields either way, just with applied_to_skeleton=False.
_PRIMITIVE_TYPES = {
    "boolean", "integer", "string", "decimal", "uri", "url", "canonical",
    "base64Binary", "instant", "date", "dateTime", "time", "code", "oid",
    "id", "markdown", "unsignedInt", "positiveInt", "xhtml",
}

_PLACEHOLDER_BY_TYPE = {
    "string": "<string>",
    "boolean": "<boolean>",
    "date": "<date>",
    "dateTime": "<dateTime>",
    "instant": "<instant>",
    "time": "<time>",
    "integer": "<integer>",
    "decimal": "<decimal>",
    "code": "<code>",
    "uri": "<uri>",
    "url": "<url>",
}
_DEFAULT_PLACEHOLDER = "<value>"


@dataclass(frozen=True)
class ResourceElementRow:
    path: str
    short: str
    type: str | None
    min_card: int
    max_card: str
    is_choice_variant: bool


@dataclass(frozen=True)
class SegmentMappingRow:
    segment: str
    field_position: int
    field_name: str
    target_path: str
    explanation: str | None
    source_url: str


@dataclass(frozen=True)
class MappedField:
    segment: str
    field_position: int
    field_name: str
    raw_value: str
    target_path: str
    explanation: str | None
    source_url: str
    applied_to_skeleton: bool


@dataclass(frozen=True)
class UnmappedField:
    segment: str
    field_position: int
    raw_value: str


@dataclass(frozen=True)
class ResourceMapping:
    resource_type: str
    skeleton: dict[str, Any]
    mapped_fields: list[MappedField]
    unmapped_fields: list[UnmappedField]


FETCH_ELEMENTS_SQL = """
SELECT path, short, type, min_card, max_card, is_choice_variant
FROM fhir_resource_elements
WHERE resource_type = %(resource_type)s;
"""

FETCH_SEGMENT_MAPPINGS_SQL = """
SELECT segment, field_position, field_name, target_path, explanation, source_url
FROM fhir_v2_segment_mappings
WHERE target_resource_type = %(resource_type)s AND segment = ANY(%(segments)s);
"""


def fetch_resource_elements(conn: psycopg.Connection, resource_type: str) -> list[ResourceElementRow]:
    with conn.cursor() as cur:
        cur.execute(FETCH_ELEMENTS_SQL, {"resource_type": resource_type})
        return [ResourceElementRow(**row) for row in cur.fetchall()]


def fetch_segment_mappings(
    conn: psycopg.Connection, resource_type: str, segments: list[str]
) -> list[SegmentMappingRow]:
    if not segments:
        return []
    with conn.cursor() as cur:
        cur.execute(FETCH_SEGMENT_MAPPINGS_SQL, {"resource_type": resource_type, "segments": segments})
        return [SegmentMappingRow(**row) for row in cur.fetchall()]


def _placeholder_for(type_code: str | None) -> str:
    if type_code is None:
        return _DEFAULT_PLACEHOLDER
    return _PLACEHOLDER_BY_TYPE.get(type_code, f"<{type_code}>")


def build_skeleton(elements: list[ResourceElementRow]) -> dict[str, Any]:
    skeleton: dict[str, Any] = {}
    for element in elements:
        if element.min_card < 1 or element.is_choice_variant:
            continue
        field_name = element.path.split(".", 1)[1]
        value: Any = _placeholder_for(element.type)
        if element.max_card == "*":
            value = [value]
        skeleton[field_name] = value
    return skeleton


def build_full_skeleton(elements: list[ResourceElementRow]) -> dict[str, Any]:
    """Every top-level field (required or not), not just the required ones --
    used when there's no sample data to ground anything against, so a
    required-only skeleton would otherwise be sparse or even empty (many
    resources, e.g. Patient, have zero required top-level elements) and not
    actually show the user "the shape of the object" they asked for.
    Choice-typed elements (Observation.value[x]) are still skipped in their
    literal form -- showing every concrete variant (valueString,
    valueQuantity, valueBoolean, ...) at once would misrepresent a real
    instance, which only ever has one."""
    skeleton: dict[str, Any] = {}
    for element in elements:
        if element.is_choice_variant:
            continue
        field_name = element.path.split(".", 1)[1]
        value: Any = _placeholder_for(element.type)
        if element.max_card == "*":
            value = [value]
        skeleton[field_name] = value
    return skeleton


def _apply_grounded_value(
    skeleton: dict[str, Any],
    elements_by_field: dict[str, ResourceElementRow],
    target_path: str,
    raw_value: str,
) -> bool:
    # Only a bare top-level field name (no ".", not the ConceptMap's "$this"
    # sentinel) with a primitive declared type gets folded in -- anything
    # else (a nested path into a complex type, or a complex type itself)
    # keeps its skeleton placeholder rather than guessing a sub-field shape
    # from raw HL7v2 text. A trailing "[N]" repetition index (e.g.
    # "identifier[1]") is stripped first -- it names which repetition, not a
    # nested path.
    if "." in target_path or target_path == "$this":
        return False
    field_name = _INDEX_SUFFIX_RE.sub("", target_path)
    element = elements_by_field.get(field_name)
    if element is None or element.type not in _PRIMITIVE_TYPES:
        return False

    value: Any = raw_value
    if element.max_card == "*":
        value = [value]
    skeleton[field_name] = value
    return True


def build_resource_mapping(
    conn: psycopg.Connection,
    resource_type: str,
    data_format: str | None,
    data_sample: str | None,
) -> ResourceMapping:
    elements = fetch_resource_elements(conn, resource_type)
    grounded = data_format == "HL7v2" and bool(data_sample)
    # A required-only skeleton stays deliberately sparse when it's about to
    # be filled in with real, cited values from the sample -- but with
    # nothing to ground, that same sparseness would just look broken (or, for
    # a resource with no required top-level fields, literally empty), so show
    # the full generic shape instead.
    skeleton = build_skeleton(elements) if grounded else build_full_skeleton(elements)

    mapped_fields: list[MappedField] = []
    unmapped_fields: list[UnmappedField] = []

    if grounded:
        elements_by_field = {e.path.split(".", 1)[1]: e for e in elements}
        parsed = parse_message(data_sample)
        segments_present = sorted({field.segment for field in parsed})
        mapping_rows = fetch_segment_mappings(conn, resource_type, segments_present)
        # One HL7v2 field can legitimately have several candidate target
        # paths (e.g. PID-7 -> both "birthDate" and an
        # "birthDate.extension[...]" precision-uncertainty variant) --
        # collapsing to a single row per (segment, field_position) would
        # silently drop alternatives and could keep an arbitrary one
        # instead of the useful bare-primitive candidate. Keep them all.
        candidates_by_key: dict[tuple[str, int], list[SegmentMappingRow]] = {}
        for row in mapping_rows:
            candidates_by_key.setdefault((row.segment, row.field_position), []).append(row)
        # Segments this resource type actually has *any* known mapping for,
        # given what's present in the sample -- a field whose segment isn't
        # relevant to this resource at all (e.g. PV1 fields on the Patient
        # card) is silently excluded rather than reported "unmapped", which
        # would wrongly imply this resource's mapping is incomplete when
        # really that field just belongs to a different resource's card.
        relevant_segments = {row.segment for row in mapping_rows}

        for field in parsed:
            if field.segment not in relevant_segments:
                continue
            candidates = candidates_by_key.get((field.segment, field.field_position))
            if not candidates:
                unmapped_fields.append(
                    UnmappedField(
                        segment=field.segment, field_position=field.field_position, raw_value=field.raw_value
                    )
                )
                continue

            already_applied = False
            for mapping in candidates:
                applied = False
                if not already_applied:
                    applied = _apply_grounded_value(
                        skeleton, elements_by_field, mapping.target_path, field.raw_value
                    )
                    already_applied = already_applied or applied
                mapped_fields.append(
                    MappedField(
                        segment=mapping.segment,
                        field_position=mapping.field_position,
                        field_name=mapping.field_name,
                        raw_value=field.raw_value,
                        target_path=mapping.target_path,
                        explanation=mapping.explanation,
                        source_url=mapping.source_url,
                        applied_to_skeleton=applied,
                    )
                )

    return ResourceMapping(
        resource_type=resource_type,
        skeleton=skeleton,
        mapped_fields=mapped_fields,
        unmapped_fields=unmapped_fields,
    )

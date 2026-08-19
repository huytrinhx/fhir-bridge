"""Deterministic HL7v2 pipe-delimited wire-syntax parsing -- segment/field
splitting only, no semantic interpretation. This is fixed HL7v2 encoding
syntax (not a modeling judgment), so it's safe to hardcode; what each field
*means* comes from backend/mapping.py matching against the grounded
v2-to-FHIR mapping table, never from this module.
"""
from __future__ import annotations

from dataclasses import dataclass

SEGMENT_ID_LENGTH = 3


@dataclass(frozen=True)
class Hl7Field:
    segment: str
    field_position: int
    raw_value: str


def split_segments(message: str) -> list[str]:
    normalized = message.replace("\r\n", "\r").replace("\n", "\r")
    return [line for line in normalized.split("\r") if line.strip()]


def parse_message(message: str) -> list[Hl7Field]:
    fields: list[Hl7Field] = []
    for line in split_segments(message):
        parts = line.split("|")
        segment = parts[0][:SEGMENT_ID_LENGTH].upper()
        if not segment:
            continue

        # MSH-1 is the field-separator character itself -- implicit, never a
        # |-delimited token -- so parts[1] (the encoding-characters token,
        # e.g. "^~\&") is MSH-2, not MSH-1. Every other segment's parts[1]
        # is field 1.
        start = 2 if segment == "MSH" else 1
        for offset, raw_value in enumerate(parts[1:]):
            if not raw_value:
                continue
            fields.append(Hl7Field(segment=segment, field_position=start + offset, raw_value=raw_value))
    return fields

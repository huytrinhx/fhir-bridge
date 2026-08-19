"""HL7's official V2-to-FHIR segment mapping IG -- the grounding source for
segment/field -> FHIR path claims (see backend/mapping.py). Downloaded via
the same generic IG-package mechanism already used for US Core
(ingestion/sources/fhir_ig.py::download_ig_package).

Package verified live: hl7.fhir.uv.v2mappings#1.0.0 (published STU1, not a
"current"/build alias), tarball at packages.simplifier.net following the
same URL shape as US_CORE, citation pages resolving under
https://hl7.org/fhir/uv/v2mappings/STU1/ -- which satisfies
backend/guardrails.py's TRUSTED_CITATION_PREFIX unmodified.
"""
from __future__ import annotations

import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingestion.sources.fhir_ig import IgPackageSource

V2_MAPPINGS = IgPackageSource(
    name="v2mappings",
    label="HL7 V2-to-FHIR",
    package_id="hl7.fhir.uv.v2mappings",
    version="1.0.0",
    tarball_url="https://packages.simplifier.net/hl7.fhir.uv.v2mappings/1.0.0",
    citation_base_url="https://hl7.org/fhir/uv/v2mappings/STU1",
)

# Confirmed by downloading and inspecting real package files: an element
# code is always "SEG-N" (segment abbreviation + 1-based field number, e.g.
# "PID-7") -- no component-level codes like "PID-5.2" exist at this
# granularity, matching this app's field-position-only schema.
_ELEMENT_CODE_RE = re.compile(r"^([A-Z0-9]+)-(\d+)$")


def load_v2_segment_concept_maps(tarball_path: Path) -> list[dict[str, Any]]:
    """Segment-level ConceptMaps only -- the same package also ships ~150
    datatype/table-level ConceptMaps (component sub-parsing, vocabulary
    translation) that this app deliberately doesn't ingest."""
    concept_maps: list[dict[str, Any]] = []
    with tarfile.open(tarball_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.name.startswith("package/ConceptMap-segment-"):
                continue
            file = archive.extractfile(member)
            if file is None:
                continue
            concept_maps.append(json.loads(file.read().decode("utf-8")))
    return concept_maps


def segment_code_from_id(concept_map_id: str) -> str | None:
    """"segment-pid-to-patient" / "segment-pid-patient-to-provenance" -> "PID"."""
    if not concept_map_id.startswith("segment-"):
        return None
    remainder = concept_map_id[len("segment-") :]
    token = remainder.split("-", 1)[0]
    return token.upper() or None


def target_resource_type_from_id(
    concept_map_id: str, resource_types_by_lower: dict[str, str]
) -> str | None:
    """The id's tail after the last "-to-" names the target resource, but
    lowercased and unhyphenated (e.g. "relatedperson") -- resolved against
    the real FHIR whitelist rather than re-capitalized by guesswork, since
    that can't be done reliably for multi-word resource type names."""
    if "-to-" not in concept_map_id:
        return None
    tail = concept_map_id.rsplit("-to-", 1)[1]
    return resource_types_by_lower.get(tail.lower())


@dataclass(frozen=True)
class V2SegmentMappingRow:
    segment: str
    field_position: int
    field_name: str
    target_resource_type: str
    target_path: str
    explanation: str | None
    source_url: str
    spec_version: str


def extract_segment_mapping_rows(
    concept_map: dict[str, Any],
    resource_types_by_lower: dict[str, str],
    citation_base_url: str,
    spec_version: str,
) -> list[V2SegmentMappingRow]:
    """Returns [] (rather than raising) for any ConceptMap whose id or
    element shape doesn't match what this app knows how to parse -- callers
    should log ids that produced zero rows so they can be inspected, not
    silently trust every file parsed cleanly."""
    concept_map_id = concept_map.get("id", "")
    segment = segment_code_from_id(concept_map_id)
    target_resource_type = target_resource_type_from_id(concept_map_id, resource_types_by_lower)
    if not segment or not target_resource_type:
        return []

    source_url = f"{citation_base_url}/ConceptMap-{concept_map_id}.html"
    rows: list[V2SegmentMappingRow] = []
    for group in concept_map.get("group", []):
        for element in group.get("element", []):
            match = _ELEMENT_CODE_RE.match(element.get("code", ""))
            if not match or match.group(1) != segment:
                continue
            field_position = int(match.group(2))
            field_name = element.get("display") or element.get("code", "")

            for target in element.get("target", []):
                target_path = target.get("code", "")
                if not target_path:
                    continue
                rows.append(
                    V2SegmentMappingRow(
                        segment=segment,
                        field_position=field_position,
                        field_name=field_name,
                        target_resource_type=target_resource_type,
                        target_path=target_path,
                        explanation=target.get("comment"),
                        source_url=source_url,
                        spec_version=spec_version,
                    )
                )
    return rows

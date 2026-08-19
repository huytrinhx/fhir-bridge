"""Extract each FHIR resource's top-level element structure (path/type/
cardinality) from the core R4 StructureDefinitions -- deterministic
structural data, used to build suggested-payload skeletons without any LLM
involvement (backend/mapping.py::build_skeleton). One chunk-free table, not
vector search: resource_type is an exact key, not a semantic query.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ingestion.config import SPEC_VERSION


@dataclass(frozen=True)
class ResourceElement:
    resource_type: str
    path: str
    short: str
    type: str | None
    min_card: int
    max_card: str
    is_choice_variant: bool = False
    spec_version: str = SPEC_VERSION


def extract_resource_elements(definitions: list[dict[str, Any]]) -> list[ResourceElement]:
    elements: list[ResourceElement] = []
    for definition in definitions:
        resource_type = definition.get("type") or definition.get("name")
        if not resource_type:
            continue

        for element in definition.get("snapshot", {}).get("element", []):
            path = element.get("path", "")
            # Top-level only: "Patient.name" (one dot), not "Patient" itself
            # or a nested "Patient.contact.name" -- finer granularity isn't
            # needed for a skeleton and would need recursive datatype
            # expansion this app deliberately doesn't do.
            if path.count(".") != 1 or not path.startswith(f"{resource_type}."):
                continue

            short = (element.get("short") or "").strip()
            min_card = element.get("min", 0)
            max_card = element.get("max", "0")
            types = element.get("type", [])

            if path.endswith("[x]"):
                # A choice-typed element (e.g. Observation.value[x]) is
                # never emitted under its literal "[x]" path -- FHIR JSON
                # always uses the concrete per-type name (valueString,
                # valueQuantity, ...), one row per declared type.
                stem = path[: -len("[x]")]
                for type_entry in types:
                    type_code = type_entry.get("code", "")
                    if not type_code:
                        continue
                    concrete_path = f"{stem}{type_code[0].upper()}{type_code[1:]}"
                    elements.append(
                        ResourceElement(
                            resource_type=resource_type,
                            path=concrete_path,
                            short=short,
                            type=type_code,
                            min_card=min_card,
                            max_card=max_card,
                            is_choice_variant=True,
                        )
                    )
                continue

            type_code = types[0].get("code") if types else None
            elements.append(
                ResourceElement(
                    resource_type=resource_type,
                    path=path,
                    short=short,
                    type=type_code,
                    min_card=min_card,
                    max_card=max_card,
                )
            )
    return elements

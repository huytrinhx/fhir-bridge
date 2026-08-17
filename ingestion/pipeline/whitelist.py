"""Build the canonical FHIR R4 resource-type whitelist used to reject any
resource name an LLM emits that isn't a real, spec-defined resource type."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ingestion.config import FHIR_R4_SPEC_BASE_URL, SPEC_VERSION


@dataclass(frozen=True)
class FhirResourceMeta:
    type_name: str
    canonical_url: str
    citation_url: str
    description: str
    spec_version: str = SPEC_VERSION


def extract_resource_metas(definitions: list[dict[str, Any]]) -> list[FhirResourceMeta]:
    metas = []
    for definition in definitions:
        type_name = definition.get("type") or definition.get("name")
        if not type_name:
            continue
        metas.append(
            FhirResourceMeta(
                type_name=type_name,
                canonical_url=definition.get("url", ""),
                citation_url=f"{FHIR_R4_SPEC_BASE_URL}/{type_name.lower()}.html",
                description=(definition.get("description") or "").strip(),
            )
        )
    return sorted(metas, key=lambda m: m.type_name)


def build_whitelist(metas: list[FhirResourceMeta]) -> set[str]:
    return {meta.type_name for meta in metas}


def save_whitelist(metas: list[FhirResourceMeta], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "spec_version": SPEC_VERSION,
        "resource_count": len(metas),
        "resources": {meta.type_name: asdict(meta) for meta in metas},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

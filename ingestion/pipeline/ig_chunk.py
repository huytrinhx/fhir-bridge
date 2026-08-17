"""Turn IG profile StructureDefinitions into retrievable chunks.

Chunks stay keyed by the base FHIR resource type a profile constrains (e.g. a
us-core-patient profile chunk is still resource_type="Patient") -- the
whitelist/guardrail only ever allow base resource type names, never profile
ids, so retrieval can surface IG-specific guidance while the model still only
ever emits real FHIR R4 resource type names.
"""
from __future__ import annotations

from typing import Any

from ingestion.pipeline.chunk import Chunk
from ingestion.sources.fhir_ig import IgPackageSource


def make_ig_chunks(source: IgPackageSource, profile: dict[str, Any]) -> list[Chunk]:
    profile_id = profile.get("id", "")
    base_type = profile.get("type", "")
    if not profile_id or not base_type:
        return []

    title = profile.get("title") or profile.get("name") or profile_id
    description = (profile.get("description") or "").strip()
    text = f"{title} ({source.label} profile of {base_type}): {description}"

    chunk = Chunk(
        id=f"ig:{source.name}:{profile_id}",
        resource_type=base_type,
        text=text,
        source_url=f"{source.citation_base_url}/StructureDefinition-{profile_id}.html",
        spec_version=source.spec_version,
        source_type="ig",
    )
    return [chunk]

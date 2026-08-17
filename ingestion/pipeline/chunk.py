"""Turn resource metadata into retrievable chunks for embedding.

One chunk per resource for now -- enough for the KB to answer "does resource
X exist, and what's it for" with a citation. Finer-grained chunking (per
element/field) can be added once the retrieval flow needs it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ingestion.pipeline.whitelist import FhirResourceMeta


@dataclass(frozen=True)
class Chunk:
    id: str
    resource_type: str
    text: str
    source_url: str
    spec_version: str
    source_type: str = "spec"


def make_chunks(meta: FhirResourceMeta) -> list[Chunk]:
    text = f"{meta.type_name}: {meta.description}".strip()
    chunk = Chunk(
        id=f"spec:{meta.spec_version}:{meta.type_name}",
        resource_type=meta.type_name,
        text=text,
        source_url=meta.citation_url,
        spec_version=meta.spec_version,
    )
    return [chunk]

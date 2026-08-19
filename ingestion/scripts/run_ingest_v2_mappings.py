"""CLI entrypoint: fetch HL7's official V2-to-FHIR segment mapping IG,
extract segment/field -> FHIR path rows, and store them as the grounding
source for the suggested-payload segment mapping feature
(backend/mapping.py). Never touches the resource-type whitelist or the
vector KB (fhir_kb_chunks) -- this is a separate, deterministic, exact-match
table (ingestion/pipeline/structural_store.py).

Usage:
    python -m ingestion.scripts.run_ingest_v2_mappings [--skip-store]
"""
from __future__ import annotations

import argparse
import sys

from ingestion.config import load_settings
from ingestion.pipeline.whitelist import extract_resource_metas
from ingestion.sources.fhir_ig import download_ig_package
from ingestion.sources.fhir_spec import download_definitions, load_resource_definitions
from ingestion.sources.fhir_v2_mappings import (
    V2_MAPPINGS,
    extract_segment_mapping_rows,
    load_v2_segment_concept_maps,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-store", action="store_true", help="skip writing to Postgres")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()

    print(f"Downloading FHIR R4 definitions into {settings.data_dir} (for resource-type resolution) ...")
    bundle_path = download_definitions(settings.data_dir)
    metas = extract_resource_metas(load_resource_definitions(bundle_path))
    resource_types_by_lower = {meta.type_name.lower(): meta.type_name for meta in metas}

    print(f"Downloading {V2_MAPPINGS.label} ({V2_MAPPINGS.package_id}#{V2_MAPPINGS.version}) ...")
    tarball_path = download_ig_package(V2_MAPPINGS, settings.data_dir)
    concept_maps = load_v2_segment_concept_maps(tarball_path)
    print(f"Loaded {len(concept_maps)} segment ConceptMaps")

    rows = []
    empty_ids = []
    for concept_map in concept_maps:
        extracted = extract_segment_mapping_rows(
            concept_map, resource_types_by_lower, V2_MAPPINGS.citation_base_url, V2_MAPPINGS.spec_version
        )
        if not extracted:
            empty_ids.append(concept_map.get("id", "<no id>"))
        rows.extend(extracted)

    print(f"Extracted {len(rows)} segment/field mapping rows")
    if empty_ids:
        # Not fatal -- some of these are known qualifier-id variants (e.g.
        # "segment-pid-patient-to-provenance") this app doesn't attempt to
        # parse -- but worth eyeballing after a run in case the id/element
        # shape assumptions were wrong for a file that should have matched.
        print(f"{len(empty_ids)} ConceptMaps produced no rows (inspect if unexpected): {empty_ids}")

    if args.skip_store:
        print("Skipping store step (--skip-store)")
        return 0

    from ingestion.pipeline.structural_store import ensure_schema, get_connection, upsert_v2_segment_mappings

    conn = get_connection(settings)
    try:
        ensure_schema(conn)
        upsert_v2_segment_mappings(conn, rows)
        print(f"Stored {len(rows)} rows in fhir_v2_segment_mappings")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

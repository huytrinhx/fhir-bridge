"""CLI entrypoint: extract each FHIR R4 resource's top-level element
structure and store it for deterministic payload-skeleton building
(backend/mapping.py). Reuses the same definitions bundle run_ingest.py
downloads (fhir_spec.download_definitions caches to disk under
INGEST_DATA_DIR, so this doesn't re-download if run_ingest.py already ran)
-- kept as its own script rather than folded into run_ingest.py's
chunk/embed/store flow, since this data needs no embedding step at all.

Usage:
    python -m ingestion.scripts.run_ingest_elements [--skip-store]
"""
from __future__ import annotations

import argparse
import sys

from ingestion.config import load_settings
from ingestion.pipeline.resource_elements import extract_resource_elements
from ingestion.sources.fhir_spec import download_definitions, load_resource_definitions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-store", action="store_true", help="skip writing to Postgres")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()

    print(f"Downloading FHIR R4 definitions into {settings.data_dir} ...")
    bundle_path = download_definitions(settings.data_dir)
    definitions = load_resource_definitions(bundle_path)
    print(f"Loaded {len(definitions)} concrete resource StructureDefinitions")

    elements = extract_resource_elements(definitions)
    print(f"Extracted {len(elements)} top-level resource elements")

    if args.skip_store:
        print("Skipping store step (--skip-store)")
        return 0

    from ingestion.pipeline.structural_store import ensure_schema, get_connection, upsert_resource_elements

    conn = get_connection(settings)
    try:
        ensure_schema(conn)
        upsert_resource_elements(conn, elements)
        print(f"Stored {len(elements)} rows in fhir_resource_elements")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

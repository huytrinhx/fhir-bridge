"""CLI entrypoint: fetch a FHIR IG package, chunk its resource profiles,
embed, and store them in the same vector KB as the core spec (Phase 0).

This never touches the resource-type whitelist -- that stays exclusively
derived from the core R4 spec (ingestion/scripts/run_ingest.py), since the
guardrail must only ever accept real FHIR R4 resource type names, never IG
profile ids. IG chunks are purely additive retrieval content, keyed by the
base resource type each profile constrains.

Usage:
    python -m ingestion.scripts.run_ingest_ig --package us_core [--skip-embed] [--skip-store]
"""
from __future__ import annotations

import argparse
import sys

from ingestion.config import load_settings
from ingestion.pipeline.chunk import Chunk
from ingestion.pipeline.ig_chunk import make_ig_chunks
from ingestion.sources.fhir_ig import US_CORE, download_ig_package, load_ig_profiles

PACKAGES = {"us_core": US_CORE}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", choices=sorted(PACKAGES), required=True)
    parser.add_argument("--skip-embed", action="store_true", help="skip Kyma embedding step")
    parser.add_argument("--skip-store", action="store_true", help="skip writing to Postgres/pgvector")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = PACKAGES[args.package]
    settings = load_settings()

    print(f"Downloading {source.label} ({source.package_id}#{source.version}) into {settings.data_dir} ...")
    tarball_path = download_ig_package(source, settings.data_dir)
    profiles = load_ig_profiles(tarball_path)
    print(f"Loaded {len(profiles)} resource profiles")

    chunks: list[Chunk] = []
    for profile in profiles:
        chunks.extend(make_ig_chunks(source, profile))
    print(f"Built {len(chunks)} chunks")

    if args.skip_embed:
        print("Skipping embedding step (--skip-embed)")
        return 0

    from ingestion.pipeline.embed import KymaEmbedder

    embedder = KymaEmbedder(settings)
    print("Embedding chunks via Kyma (Qwen3 Embedding 8B)...")
    embeddings = embedder.embed([chunk.text for chunk in chunks])

    if args.skip_store:
        print("Skipping store step (--skip-store)")
        return 0

    from ingestion.pipeline.store import ensure_schema, get_connection, upsert_chunks

    conn = get_connection(settings)
    try:
        ensure_schema(conn)
        upsert_chunks(conn, chunks, embeddings)
        print(f"Stored {len(chunks)} chunks in fhir_kb_chunks")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())

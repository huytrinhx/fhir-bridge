"""CLI entrypoint: fetch FHIR R4 resource definitions, build the resource
whitelist, chunk, embed, and store them in the vector KB.

Usage:
    python -m ingestion.scripts.run_ingest [--skip-embed] [--skip-store]
"""
from __future__ import annotations

import argparse
import sys

from ingestion.config import load_settings
from ingestion.pipeline.chunk import Chunk, make_chunks
from ingestion.pipeline.whitelist import extract_resource_metas, save_whitelist
from ingestion.sources.fhir_spec import download_definitions, load_resource_definitions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-embed", action="store_true", help="skip Voyage embedding step")
    parser.add_argument("--skip-store", action="store_true", help="skip writing to Postgres/pgvector")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()

    print(f"Downloading FHIR R4 definitions into {settings.data_dir} ...")
    bundle_path = download_definitions(settings.data_dir)
    definitions = load_resource_definitions(bundle_path)
    print(f"Loaded {len(definitions)} concrete resource StructureDefinitions")

    metas = extract_resource_metas(definitions)
    whitelist_path = settings.data_dir / "whitelist_r4.json"
    save_whitelist(metas, whitelist_path)
    print(f"Wrote resource whitelist ({len(metas)} types) to {whitelist_path}")

    chunks: list[Chunk] = []
    for meta in metas:
        chunks.extend(make_chunks(meta))
    print(f"Built {len(chunks)} chunks")

    if args.skip_embed:
        print("Skipping embedding step (--skip-embed)")
        return 0

    from ingestion.pipeline.embed import OpenAIEmbedder

    embedder = OpenAIEmbedder(settings)
    print(f"Embedding chunks via OpenAI ({settings.embedding_model})...")
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

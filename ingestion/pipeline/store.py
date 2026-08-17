"""Postgres/pgvector storage for embedded KB chunks."""
from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector

from ingestion.config import Settings
from ingestion.pipeline.chunk import Chunk

# No ANN index (HNSW/IVFFlat) on embedding: Qwen3 Embedding 8B's 4096 dims
# exceeds pgvector's indexable limit (2000 dims, 4000 for halfvec). Exact
# search via `ORDER BY embedding <=> :query` is a sequential scan, which is
# fine at our current/near-term corpus size (low thousands of chunks). If the
# corpus grows past ~50k chunks, revisit: switch to a lower-dim model, or
# reduce via halfvec truncation, and add an index then.
SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS fhir_kb_chunks (
    id TEXT PRIMARY KEY,
    resource_type TEXT NOT NULL,
    spec_version TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR(4096) NOT NULL
);
"""

UPSERT_SQL = """
INSERT INTO fhir_kb_chunks (id, resource_type, spec_version, source_type, source_url, text, embedding)
VALUES (%(id)s, %(resource_type)s, %(spec_version)s, %(source_type)s, %(source_url)s, %(text)s, %(embedding)s)
ON CONFLICT (id) DO UPDATE SET
    resource_type = EXCLUDED.resource_type,
    spec_version = EXCLUDED.spec_version,
    source_type = EXCLUDED.source_type,
    source_url = EXCLUDED.source_url,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding;
"""


def get_connection(settings: Settings) -> psycopg.Connection:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(settings.database_url)


def ensure_schema(conn: psycopg.Connection) -> None:
    # CREATE EXTENSION must run before register_vector, which looks up the
    # "vector" type in the database -- it doesn't exist until this runs once.
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()
    register_vector(conn)


def upsert_chunks(conn: psycopg.Connection, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
    rows = [
        {
            "id": chunk.id,
            "resource_type": chunk.resource_type,
            "spec_version": chunk.spec_version,
            "source_type": chunk.source_type,
            "source_url": chunk.source_url,
            "text": chunk.text,
            "embedding": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    conn.commit()

"""Query-time retrieval against the FHIR R4 knowledge base."""
from __future__ import annotations

from dataclasses import dataclass

import psycopg
from openai import OpenAI
from pgvector.psycopg import Vector, register_vector

from backend.config import Settings

SEARCH_SQL = """
SELECT resource_type, text, source_url, spec_version, embedding <=> %(query_embedding)s AS distance
FROM fhir_kb_chunks
ORDER BY distance ASC
LIMIT %(k)s;
"""


@dataclass(frozen=True)
class RetrievedChunk:
    resource_type: str
    text: str
    source_url: str
    spec_version: str
    distance: float


class FhirRetriever:
    def __init__(self, settings: Settings):
        if not settings.kyma_api_key:
            raise RuntimeError("KYMA_API_KEY is not set")
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        self._embed_client = OpenAI(
            api_key=settings.kyma_api_key, base_url=settings.kyma_embeddings_base_url
        )
        self._embedding_model = settings.embedding_model
        self._database_url = settings.database_url

    def _embed_query(self, query: str) -> list[float]:
        result = self._embed_client.embeddings.create(model=self._embedding_model, input=[query])
        return result.data[0].embedding

    def search(self, query: str, k: int = 8) -> list[RetrievedChunk]:
        query_embedding = self._embed_query(query)
        with psycopg.connect(self._database_url) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                # Vector(...) forces the pgvector type dumper -- a bare list is
                # ambiguous outside an INSERT's column-typed context and gets
                # sent as a Postgres double precision[] instead, which <=>
                # doesn't accept.
                cur.execute(
                    SEARCH_SQL, {"query_embedding": Vector(query_embedding), "k": k}
                )
                rows = cur.fetchall()
        return [
            RetrievedChunk(
                resource_type=row[0],
                text=row[1],
                source_url=row[2],
                spec_version=row[3],
                distance=row[4],
            )
            for row in rows
        ]

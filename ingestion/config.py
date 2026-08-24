"""Environment-driven configuration for the ingestion pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SPEC_VERSION = "R4"
FHIR_R4_DEFINITIONS_URL = "https://hl7.org/fhir/R4/definitions.json.zip"
FHIR_R4_SPEC_BASE_URL = "https://hl7.org/fhir/R4"

# text-embedding-3-small via OpenAI directly (not Kyma) -- 1536-dim, under
# pgvector's HNSW/IVFFlat indexable limit (2000 dims, 4000 for halfvec), so
# an ANN index becomes an option later if the corpus outgrows exact search
# (see ingestion/pipeline/store.py). Update this AND the VECTOR(...) column
# width there together if OPENAI_EMBEDDING_MODEL changes -- the KB needs
# re-ingesting either way, since old and new embeddings aren't comparable.
EMBEDDING_DIMENSIONS = 1536

# Inference (intent gate, clarification, answer synthesis -- Phase 1+) will
# route through Kyma's Anthropic-compatible /v1/messages endpoint using the
# same KYMA_API_KEY. Not wired up yet; ingestion only needs embeddings.


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str | None
    openai_api_key: str | None
    embedding_model: str


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("INGEST_DATA_DIR", "./data")),
        database_url=os.environ.get("DATABASE_URL"),
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        embedding_model=os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
    )

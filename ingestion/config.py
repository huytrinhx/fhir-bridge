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

# Qwen3 Embedding 8B via Kyma: strongest retrieval quality of Kyma's catalog.
# Outputs 4096-dim vectors, which exceeds pgvector's HNSW/IVFFlat indexable
# limit (2000 dims, 4000 for halfvec) -- see ingestion/pipeline/store.py for
# how that's handled (exact search, no ANN index, fine at our corpus size).
EMBEDDING_DIMENSIONS = 4096

# Inference (intent gate, clarification, answer synthesis -- Phase 1+) will
# route through Kyma's Anthropic-compatible /v1/messages endpoint using the
# same KYMA_API_KEY. Not wired up yet; ingestion only needs embeddings.


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str | None
    kyma_api_key: str | None
    kyma_embeddings_base_url: str
    embedding_model: str


def load_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("INGEST_DATA_DIR", "./data")),
        database_url=os.environ.get("DATABASE_URL"),
        kyma_api_key=os.environ.get("KYMA_API_KEY"),
        kyma_embeddings_base_url=os.environ.get(
            "KYMA_EMBEDDINGS_BASE_URL", "https://api.kymaapi.com/v1"
        ),
        embedding_model=os.environ.get("KYMA_EMBEDDING_MODEL", "qwen3-embedding-8b"),
    )

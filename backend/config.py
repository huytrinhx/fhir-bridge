"""Environment-driven configuration for the runtime backend (retrieval + inference)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    kyma_api_key: str | None
    kyma_embeddings_base_url: str
    kyma_messages_base_url: str
    embedding_model: str
    intent_model: str
    synth_model: str
    whitelist_path: Path
    admin_email: str | None


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL"),
        kyma_api_key=os.environ.get("KYMA_API_KEY"),
        kyma_embeddings_base_url=os.environ.get(
            "KYMA_EMBEDDINGS_BASE_URL", "https://api.kymaapi.com/v1"
        ),
        kyma_messages_base_url=os.environ.get("KYMA_MESSAGES_BASE_URL", "https://kymaapi.com"),
        embedding_model=os.environ.get("KYMA_EMBEDDING_MODEL", "qwen3-embedding-8b"),
        intent_model=os.environ.get("KYMA_INTENT_MODEL", "claude-sonnet-5"),
        synth_model=os.environ.get("KYMA_SYNTH_MODEL", "qwen3.7-flash"),
        whitelist_path=Path(os.environ.get("INGEST_DATA_DIR", "./data")) / "whitelist_r4.json",
        # The one user whose email matches this (case-insensitively) sees the
        # admin view (quality reports, default-model settings) after signing
        # up/logging in like anyone else -- see backend/auth.py::is_admin_email.
        admin_email=os.environ.get("ADMIN_EMAIL"),
    )

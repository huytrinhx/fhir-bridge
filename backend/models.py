"""Kyma model catalog for the frontend's model dropdown.

Full catalog per user decision (not just the vetted Claude family) -- an
in-process cache avoids hitting Kyma's /models endpoint on every page load.
"""
from __future__ import annotations

import time

from openai import OpenAI

from backend.config import Settings

CACHE_TTL_SECONDS = 600

_cache: dict[str, object] = {"models": None, "fetched_at": 0.0}

# Kyma's /models catalog is multi-modal: embedding/rerank, image, video,
# speech/music/voice, and transcription models all come back in the same
# list as chat models. None of those can run this app's tool-use loop, so
# they're filtered by keyword rather than hand-maintaining an allowlist that
# goes stale as Kyma's chat-model lineup changes.
_NON_CHAT_KEYWORDS = (
    "embed", "rerank", "bge", "minilm", "gte-", "e5-large",
    "flux", "imagen", "recraft", "ideogram", "nano-banana", "minimax-image",
    "hailuo", "kling", "veo-", "seedance",
    "eleven", "whisper", "voice", "speech", "music", "muse-spark",
    "transcribe", "realtime-translate", "live-translate", "live-preview",
    "-audio", "gpt-image",
)


def _is_chat_model(model_id: str) -> bool:
    return not any(keyword in model_id for keyword in _NON_CHAT_KEYWORDS)


def list_available_models(settings: Settings) -> list[str]:
    now = time.time()
    cached = _cache["models"]
    if cached is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return cached  # type: ignore[return-value]

    client = OpenAI(api_key=settings.kyma_api_key, base_url=settings.kyma_embeddings_base_url)
    all_models = [m.id for m in client.models.list()]
    models = sorted(m for m in all_models if _is_chat_model(m))

    _cache["models"] = models
    _cache["fetched_at"] = now
    return models

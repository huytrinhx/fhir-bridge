"""Kyma API embedding client for KB chunks (OpenAI-compatible /embeddings)."""
from __future__ import annotations

from openai import OpenAI
from tqdm import tqdm

from ingestion.config import Settings


class KymaEmbedder:
    def __init__(self, settings: Settings, batch_size: int = 128):
        if not settings.kyma_api_key:
            raise RuntimeError("KYMA_API_KEY is not set")
        self._client = OpenAI(
            api_key=settings.kyma_api_key, base_url=settings.kyma_embeddings_base_url
        )
        self._model = settings.embedding_model
        self._batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batches = range(0, len(texts), self._batch_size)
        for i in tqdm(batches, desc="Embedding chunks"):
            batch = texts[i : i + self._batch_size]
            result = self._client.embeddings.create(model=self._model, input=batch)
            ordered = sorted(result.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors

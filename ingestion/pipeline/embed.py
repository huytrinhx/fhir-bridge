"""OpenAI embedding client for KB chunks -- talks to OpenAI's API directly,
not through Kyma (which remains the LLM-inference provider elsewhere in this
app)."""
from __future__ import annotations

from openai import OpenAI
from tqdm import tqdm

from ingestion.config import Settings


class OpenAIEmbedder:
    def __init__(self, settings: Settings, batch_size: int = 128):
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self._client = OpenAI(api_key=settings.openai_api_key)
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

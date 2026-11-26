"""
Unified dense text EmbeddingAdapter implementations (EVO-P3-10):
- ``OpenAIEmbeddingAdapter`` (``text-embedding-3-small`` / ``text-embedding-3-large``)
- ``GeminiEmbeddingAdapter`` (``text-embedding-004`` via ``x-goog-api-key``)
- ``SentenceTransformerEmbeddingAdapter`` (optional local neural embeddings)
- ``LocalHashEmbeddingAdapter`` (zero-cost deterministic offline fallback)
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from product_value_system.caching.similarity_matcher import local_prompt_embedding
from product_value_system.secrets.vault_client import resolve_secret

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingAdapter(Protocol):
    """Protocol for dense vector embedding providers."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class LocalHashEmbeddingAdapter:
    """Zero-API-cost deterministic hashed n-gram + word embedding fallback."""

    def __init__(self, *, dimension: int = 256, model_name: str = "local-fnv1a-256") -> None:
        self._dimension = max(16, int(dimension))
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        return local_prompt_embedding(text, dim=self._dimension)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [local_prompt_embedding(t, dim=self._dimension) for t in texts]


class OpenAIEmbeddingAdapter:
    """OpenAI ``/v1/embeddings`` dense vector adapter (supports custom httpx transport)."""

    MODEL_DIMENSIONS: dict[str, int] = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        dimensions: int | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved = resolve_secret(
            vault_path="product-value-system/openai",
            key="api_key",
            env_vars=("OPENAI_API_KEY",),
            explicit=api_key,
        )
        if not resolved:
            raise ValueError("OpenAI API key required for OpenAIEmbeddingAdapter")
        self._api_key = resolved
        self._model = model
        self._dimension = int(dimensions or self.MODEL_DIMENSIONS.get(model, 1536))
        self._requested_dimensions = dimensions
        client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "headers": {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0] if vectors else [0.0] * self._dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self._model, "input": texts}
        if self._requested_dimensions is not None:
            payload["dimensions"] = self._requested_dimensions
        response = await self._client.post("/embeddings", json=payload)
        response.raise_for_status()
        data = response.json()
        items = sorted(data.get("data", []), key=lambda item: int(item.get("index", 0)))
        return [[float(x) for x in item["embedding"]] for item in items]

    async def close(self) -> None:
        await self._client.aclose()


class GeminiEmbeddingAdapter:
    """Google Gemini ``:embedContent`` / ``:batchEmbedContents`` dense vector adapter."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str = "text-embedding-004",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        dimension: int = 768,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved = resolve_secret(
            vault_path="product-value-system/gemini",
            key="api_key",
            env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            explicit=api_key,
        )
        if not resolved:
            raise ValueError("Gemini API key required for GeminiEmbeddingAdapter")
        self._api_key = resolved
        self._model = model
        self._dimension = int(dimension)
        client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "headers": {
                "x-goog-api-key": self._api_key,
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        payload = {"content": {"parts": [{"text": text}]}}
        response = await self._client.post(f"/models/{self._model}:embedContent", json=payload)
        response.raise_for_status()
        data = response.json()
        values = (data.get("embedding") or {}).get("values") or []
        return [float(x) for x in values]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        requests = [
            {"model": f"models/{self._model}", "content": {"parts": [{"text": t}]}}
            for t in texts
        ]
        response = await self._client.post(
            f"/models/{self._model}:batchEmbedContents",
            json={"requests": requests},
        )
        response.raise_for_status()
        data = response.json()
        embeddings = data.get("embeddings") or []
        return [[float(x) for x in item.get("values", [])] for item in embeddings]

    async def close(self) -> None:
        await self._client.aclose()


class SentenceTransformerEmbeddingAdapter:
    """Optional local neural embeddings powered by ``sentence-transformers``."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", *, dimension: int = 384) -> None:
        self._model_name = model_name
        self._dimension = int(dimension)
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise ImportError(
                    "sentence-transformers is required for SentenceTransformerEmbeddingAdapter. "
                    "Install with: pip install sentence-transformers"
                ) from exc
            self._model = SentenceTransformer(self._model_name)
        return self._model

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0] if vectors else [0.0] * self._dimension

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        encoded = model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in row] for row in encoded]


def create_embedding_adapter(provider: str = "local", **kwargs: Any) -> EmbeddingAdapter:
    """Factory creating a concrete ``EmbeddingAdapter`` by provider identifier."""
    normalized = provider.strip().lower().replace("-", "_")
    if normalized == "openai":
        return OpenAIEmbeddingAdapter(**kwargs)
    if normalized in {"gemini", "google"}:
        return GeminiEmbeddingAdapter(**kwargs)
    if normalized in {"sentence_transformers", "st", "local_neural"}:
        return SentenceTransformerEmbeddingAdapter(**kwargs)
    if normalized in {"local", "hash", "offline"}:
        return LocalHashEmbeddingAdapter(**kwargs)
    raise ValueError(
        f"Unsupported embedding provider: {provider!r}. "
        "Expected one of: openai, gemini, sentence_transformers, local."
    )

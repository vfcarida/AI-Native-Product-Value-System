"""
Semantic prompt similarity matching for LLM token caching (EVO-025).

Supports custom async/sync embedding providers (e.g., OpenAI ``text-embedding-3-small``,
Sentence-Transformers) as well as a fast deterministic local n-gram + word-frequency
embedding model for zero-API-cost offline matching.
"""

from __future__ import annotations

import inspect
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable

EmbeddingFunc = Callable[[str], Awaitable[list[float]] | list[float]]

_WORD_RE = re.compile(r"[a-z0-9_]+")
_DIM = 256


def local_prompt_embedding(text: str, dim: int = _DIM) -> list[float]:
    """Deterministic hashed word + character trigram embedding normalized to unit length."""
    cleaned = " ".join(text.lower().split())
    if not cleaned:
        return [0.0] * dim

    vec = [0.0] * dim
    words = [w for w in _WORD_RE.findall(cleaned) if len(w) >= 2]
    word_counts = Counter(words)
    for word, count in word_counts.items():
        # Deterministic FNV-1a-style hash so results are stable across Python processes
        h = _stable_hash(f"w:{word}")
        idx = h % dim
        sign = 1.0 if ((h >> 16) & 1) == 0 else -1.0
        vec[idx] += sign * (1.0 + math.log1p(count))

    # Character trigrams capture lexical/structural similarity across minor rewrites
    if len(cleaned) >= 3:
        trigrams = Counter(cleaned[i : i + 3] for i in range(len(cleaned) - 2))
        for tri, count in trigrams.items():
            h = _stable_hash(f"c:{tri}")
            idx = h % dim
            sign = 1.0 if ((h >> 16) & 1) == 0 else -1.0
            vec[idx] += 0.15 * sign * (1.0 + math.log1p(count))

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0.0:
        return [x / norm for x in vec]
    return vec


def _stable_hash(value: str) -> int:
    h = 2166136261
    for b in value.encode("utf-8"):
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity in [-1.0, 1.0] safely guarding against zero vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    raw = dot / (norm_a * norm_b)
    return max(-1.0, min(1.0, raw))


class SimilarityMatcher:
    """Embed prompts and locate the highest-similarity cached entry above threshold."""

    def __init__(self, embedding_func: EmbeddingFunc | None = None) -> None:
        self._embedding_func: EmbeddingFunc = embedding_func or local_prompt_embedding

    async def embed(self, text: str) -> list[float]:
        """Generate normalized embedding vector for ``text``."""
        res = self._embedding_func(text)
        if inspect.isawaitable(res):
            return [float(x) for x in await res]
        return [float(x) for x in res]

    def find_most_similar(
        self,
        query_embedding: list[float],
        candidates: list[tuple[str, list[float]]],
        threshold: float = 0.95,
    ) -> tuple[str, float] | None:
        """Return ``(cache_key, similarity)`` of best candidate ``>= threshold``, else ``None``."""
        best_key: str | None = None
        best_score = threshold

        for key, candidate_vec in candidates:
            score = cosine_similarity(query_embedding, candidate_vec)
            if score >= best_score:
                best_score = score
                best_key = key

        if best_key is None:
            return None
        return best_key, best_score

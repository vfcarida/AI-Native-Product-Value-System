"""
LLM Token & Semantic Prompt Caching module (EVO-025).

Provides exact-match + semantic similarity caching with Memory (LRU), SQLite,
Redis, and Tiered L1/L2 backends, transparent OpenAI/Anthropic adapter integration,
TTL expiration, and model-version invalidation.
"""

from product_value_system.caching.cache_backends import (
    CacheBackend,
    CachedResponse,
    MemoryCacheBackend,
    RedisCacheBackend,
    SQLiteCacheBackend,
    TieredCacheBackend,
)
from product_value_system.caching.prompt_cache import (
    PromptCache,
    cached_llm_call,
    deserialize_model_response,
    serialize_model_response,
)
from product_value_system.caching.similarity_matcher import (
    SimilarityMatcher,
    cosine_similarity,
    local_prompt_embedding,
)

__all__ = [
    "CacheBackend",
    "CachedResponse",
    "MemoryCacheBackend",
    "PromptCache",
    "RedisCacheBackend",
    "SQLiteCacheBackend",
    "SimilarityMatcher",
    "TieredCacheBackend",
    "cached_llm_call",
    "cosine_similarity",
    "deserialize_model_response",
    "local_prompt_embedding",
    "serialize_model_response",
]

# LLM Token & Semantic Prompt Caching Guide (EVO-025)

The Product Value System includes a transparent **hybrid exact + semantic LLM response cache** (`PromptCache`) to eliminate redundant model API calls across agent runs while preserving full `ModelResponse` and token `Usage` accounting.

---

## Architecture

1. **Exact Match Fast Path (`<1ms`)**: Computes a deterministic `xxhash64` key from normalized `messages`, `model`, `temperature` (rounded to 2 decimal places), `max_tokens`, `tools`, and optional `model_version`.
2. **Semantic Similarity Fallback (`temperature < 0.3`)**: When exact match misses on deterministic calls, `SimilarityMatcher` embeds the prompt (using a custom embedding callable such as OpenAI `text-embedding-3-small` or the built-in local n-gram + word embedding) and reuses a same-model response when cosine similarity `>= similarity_threshold` (default `0.95`).
3. **Configurable Backends**:
   - `MemoryCacheBackend`: Async-safe `OrderedDict` with LRU eviction (`max_entries`).
   - `SQLiteCacheBackend`: Persistent local SQLite cache with LRU eviction (`last_accessed` index).
   - `RedisCacheBackend`: Distributed Redis backend with native TTL expiry (`ex=ttl_seconds`).
   - `TieredCacheBackend`: Multi-level L1 in-memory + L2 persistent backend with automatic L1 backfill.
4. **TTL & Model Version Invalidation**: Expired entries are pruned automatically on lookup, and `cache.invalidate_model(model)` purges stale entries when upgrading model versions.

---

## Configuration (`config/caching.yaml`)

See [`config/caching.yaml`](../config/caching.yaml):

```yaml
caching:
  enabled: true
  backend: memory  # memory | sqlite | redis | tiered
  ttl_seconds: 604800
  similarity_threshold: 0.95
  max_entries: 10000
  invalidate_on_model_change: true
```

---

## Adapter Usage

### OpenAI & Anthropic Adapters

```python
from product_value_system.adapters import AnthropicAdapter, OpenAIModelAdapter
from product_value_system.caching import PromptCache, SQLiteCacheBackend

cache = PromptCache(
    backend=SQLiteCacheBackend(".cache/llm_cache.db", max_entries=10000),
    ttl_seconds=604800,
    similarity_threshold=0.95,
)

openai_adapter = OpenAIModelAdapter(model="gpt-4o", cache=cache)
anthropic_adapter = AnthropicAdapter(model="claude-3-5-sonnet-20241022", cache=cache)

# Deterministic calls (temperature < 0.3) automatically use the cache
resp = await openai_adapter.invoke(
    [{"role": "user", "content": "Generate unit tests for add(a, b)"}],
    temperature=0.0,
)
print(cache.get_stats())
```

### Rollback / Disable Caching

Set `enabled: false` in `config/caching.yaml` or omit `cache=` when constructing adapters.

---

## Installation

```bash
pip install -e ".[caching]"
```

See also: [README](../README.md), [Cost Management](COST_MANAGEMENT.md), [OpenTelemetry GenAI Conventions](otel-genai-conventions.md).

# ADR-0004: FinOps Caching, Model Routing, Batching, and Resilience Stack

**Status**: Accepted  
**Date**: 2026-11-22  
**Authors**: @architecture-team  
**Related Requirements**: REQ-154, GAP-TOKEN-CACHING, GAP-MODEL-ROUTING, GAP-EVAL-BATCHING  
**Related Code**: `src/product_value_system/caching/prompt_cache.py`, `src/product_value_system/routing/model_router.py`, `src/product_value_system/evaluation/batch_runner.py`, `src/product_value_system/harness/resilience.py`  

## Context

Unoptimized multi-agent value streams invoke flagship frontier models sequentially on every task—even for deterministic schema formatting or repeated golden evaluation samples—resulting in excessive LLM spend and cascading outages during provider rate-limit (`HTTP 429`) storms. FinOps Foundation benchmarks and AWS Builders' Library resilience studies demonstrate that cost efficiency and fault isolation must be architectural primitives rather than afterthoughts.

## Decision

We standardize a four-layer FinOps and resilience plane across all LLM invocations:

1. **Tiered Prompt & Semantic Caching** (`PromptCache` over Memory LRU L1, WAL-tuned SQLite L2, or Redis) for deterministic prompts (`temperature < 0.3`).
2. **Complexity-Aware Model Routing** (`ModelRouter` + 6-feature `ComplexityClassifier`) routing LOW/MEDIUM/HIGH tasks to cost-optimal tiers with automated higher-tier fallback on quality validation failure.
3. **Discounted Batch Evaluation** (`BatchEvaluationRunner`) routing golden dataset suites exceeding threshold size through OpenAI Batch API (50% discount) and Anthropic Message Batches (30% discount) with realtime fallback.
4. **AWS Builders' Library Resilience** (`CircuitBreaker`, `RetryTokenBucket`, `full_jitter_delay`, `execute_with_resilience`) preventing thundering herds and retry amplification.

## Alternatives Considered

### 1. Always Route to Single Flagship Model
**Pros**: Simple adapter wiring.  
**Cons**: 5x–10x higher token spend on low-complexity extraction/formatting tasks (empirical benchmark shows 90.8% cost reduction with complexity routing).  
**Verdict**: Rejected.

### 2. Fixed Linear Backoff Retries Without Circuit Breakers
**Pros**: Minimal code.  
**Cons**: Synchronized retry storms amplify provider outages and exhaust agent latency budgets.  
**Verdict**: Rejected in favor of full-jitter exponential backoff + token-bucket retry budgets + 3-state circuit breaker.

## Consequences

### Positive
- Verified 90.8% routing cost reduction vs. always-flagship baseline at 4,404 routing decisions/sec (see [BENCHMARKS.md](../BENCHMARKS.md)).
- 10.6 µs p50 L1 memory cache hits and WAL-tuned persistent SQLite L2 storage.
- 30%–50% evaluation cost reduction via native batch API adapters.
- Fast-fail circuit isolation protects orchestrator latency SLOs during upstream provider degradation.

### Negative
- Requires strict version-keyed cache invalidation when upgrading model checkpoints (`invalidate_model()`).
- Batch evaluations trade synchronous latency for asynchronous 24h SLA cost savings (mitigated via `realtime_threshold_samples`).

## Validation

- [x] Automated unit & integration suites pass (`tests/unit/test_prompt_cache.py`, `tests/unit/test_model_router.py`, `tests/unit/test_batch_runner.py`, `tests/unit/test_resilience.py`)
- [x] Reproducible zero-cost FinOps benchmarks verified via `python scripts/run_benchmarks.py` and `tests/unit/test_benchmark_runner.py`

## References
- [Token Caching Guide](../token-caching.md)
- [Model Routing Guide](../model-routing.md)
- [Batch Evaluations Guide](../batch-evaluations.md)
- [Empirical Benchmarks](../BENCHMARKS.md)

## Metadata

```yaml
id: ADR-0004
status: accepted
date: '2026-11-22'
authors:
- architecture-team
supersedes: []
related_requirements:
- REQ-154
- GAP-TOKEN-CACHING
- GAP-MODEL-ROUTING
- GAP-EVAL-BATCHING
related_code:
- src/product_value_system/caching/prompt_cache.py
- src/product_value_system/routing/model_router.py
- src/product_value_system/evaluation/batch_runner.py
- src/product_value_system/harness/resilience.py
tags:
- finops
- caching
- routing
- resilience
```

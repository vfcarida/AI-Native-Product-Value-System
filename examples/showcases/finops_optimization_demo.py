#!/usr/bin/env python3
"""
Showcase 1: FinOps Optimization & Resilience Stack ($0 API Cost)

Demonstrates EVO-025 (Prompt Caching), EVO-026 (Complexity Model Routing),
EVO-027 (Batch Evaluation Savings), and AWS Builders' Library Circuit Breaker
+ Full-Jitter Retries running completely offline.

Run:
    python examples/showcases/finops_optimization_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from product_value_system.adapters.model_adapter import (  # noqa: E402
    Message,
    ModelResponse,
    Role,
    Usage,
)
from product_value_system.caching import (  # noqa: E402
    MemoryCacheBackend,
    PromptCache,
    deserialize_model_response,
    serialize_model_response,
)
from product_value_system.evaluation.batch_runner import estimate_batch_savings  # noqa: E402
from product_value_system.harness.resilience import (  # noqa: E402
    CircuitBreaker,
    execute_with_resilience,
)
from product_value_system.routing import ModelRouter  # noqa: E402


async def run_demo() -> None:
    print("=== Showcase 1: FinOps Routing, Caching, Batching & Resilience ($0 Cost) ===\n")

    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    cache = PromptCache(backend=MemoryCacheBackend(max_entries=128))
    breaker = CircuitBreaker(name="demo-llm", failure_threshold=3, recovery_timeout_seconds=10.0)

    tasks = [
        ("format_conversion", "Convert 'id,name\n1,checkout' into valid JSON."),
        ("code_review", "Review this Python retry handler for idempotency edge cases."),
        (
            "architecture_design",
            "Design a multi-region event-sourced ledger with zero-downtime migration.",
        ),
        ("format_conversion", "Convert 'id,name\n1,checkout' into valid JSON."),  # Cache hit!
    ]

    attempts = {"count": 0}
    for idx, (task_type, prompt) in enumerate(tasks, start=1):
        msgs = [Message(role=Role.USER, content=prompt)]
        decision = router.route(msgs, task_type=task_type)
        cached = await cache.get(msgs, decision.model, 0.0, 256)

        if cached is not None:
            resp = deserialize_model_response(cached)
            source = "CACHE_HIT"
        else:
            async def _simulated_call(model_name: str = decision.model) -> ModelResponse:
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise RuntimeError("HTTP 429 rate limit exceeded (simulated transient)")
                return ModelResponse(
                    content=f"Handled by {model_name}",
                    model=model_name,
                    usage=Usage(input_tokens=200, output_tokens=80),
                    finish_reason="stop",
                )

            resp = await execute_with_resilience(
                _simulated_call,
                circuit_breaker=breaker,
                max_attempts=3,
                base_delay_seconds=0.001,
                max_delay_seconds=0.005,
            )
            await cache.set(msgs, decision.model, 0.0, 256, serialize_model_response(resp))
            source = "PROVIDER (resilient)"

        print(
            f"Task {idx} [{task_type:19s}] -> tier={decision.complexity_level.value:6s} | "
            f"model={decision.model:27s} | {source}"
        )

    stats = cache.get_stats()
    rt, batch, saved, pct = estimate_batch_savings(
        provider="openai", model="gpt-4o", input_tokens=600_000, output_tokens=200_000
    )
    print(f"\nCache Summary   : hits={stats['hits']}, misses={stats['misses']}")
    print(f"Circuit Breaker : state={breaker.get_stats().state} (transient 429 recovered)")
    print(
        f"Batch Eval (500): realtime=${rt:.2f} -> batch=${batch:.2f} "
        f"(saved ${saved:.2f}, {pct:.0f}%)"
    )


if __name__ == "__main__":
    asyncio.run(run_demo())

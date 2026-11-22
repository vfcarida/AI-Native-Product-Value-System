# Batch Evaluations Guide (EVO-027)

Batch evaluation processing reduces LLM-as-judge evaluation costs by **30–50%** by routing large evaluation datasets through asynchronous provider batch APIs while keeping small PR and smoke-test runs fast via real-time execution.

---

## Architecture Overview

```
Golden Dataset (N samples)
       │
       ▼
  EvalRunner  ── (N < realtime_threshold_samples OR enabled: false) ──▶ Real-Time Judge (low latency)
       │
       │ (N >= realtime_threshold_samples AND enabled: true)
       ▼
 BatchEvalRunner
   ├─ Split into JSONL chunks (batch_size, e.g. 50)
   ├─ Concurrent submission (max_concurrent_batches) via:
   │    • OpenAIBatchAdapter    (POST /v1/files + /v1/batches, 50% discount, 24h SLA)
   │    • AnthropicBatchAdapter (POST /v1/messages/batches, 30% discount)
   ├─ BatchPoller (concurrent status polling, progress callback, timeout guard)
   ├─ Result parser (matches custom_id → sample order, extracts token usage)
   ├─ Automatic real-time fallback for failed/expired batches (optional)
   └─ BatchCostSummary (real-time baseline vs. batch cost & % savings)
```

Core modules:
- [src/product_value_system/evaluation/batch_adapters.py](../src/product_value_system/evaluation/batch_adapters.py) — `BatchAdapter`, `OpenAIBatchAdapter`, `AnthropicBatchAdapter`, `BatchJob`, `BatchResult`
- [src/product_value_system/evaluation/batch_poller.py](../src/product_value_system/evaluation/batch_poller.py) — `BatchPoller`, `BatchPollTimeoutError`
- [src/product_value_system/evaluation/batch_runner.py](../src/product_value_system/evaluation/batch_runner.py) — `BatchEvalRunner`, `BatchEvalReport`, `BatchCostSummary`, `estimate_batch_savings()`
- [src/product_value_system/evaluation/eval_runner.py](../src/product_value_system/evaluation/eval_runner.py) — Hybrid `EvalRunner` and `EvalRunSummary`
- [config/evaluation.yaml](../config/evaluation.yaml) — YAML configuration

---

## Provider Comparison & Cost Savings

| Provider | Adapter | Batch Discount | Max Requests / Batch | SLA Window |
|---|---|---:|---:|---|
| OpenAI Batch API | `OpenAIBatchAdapter` | **50%** | 50,000 | 24 hours |
| Anthropic Message Batches | `AnthropicBatchAdapter` | **30%** | 10,000 | Async priority |

### Example Economics (1,000-Sample Golden Dataset, GPT-4 Turbo Judge)

Assuming ~1,000 input tokens and ~200 output tokens per judge call:
- **Real-time cost**: 1M input (`$10.00`) + 200K output (`$6.00`) = **`$16.00` / run**
- **OpenAI Batch API cost (50% discount)**: **`$8.00` / run** (`$8.00` saved per run, or **`$240/month`** on nightly runs)
- **Anthropic Message Batches cost (30% discount)** on Claude 3.5 Sonnet: 30% reduction on every nightly or release-gate evaluation.

---

## Hybrid CI Strategy (Recommended)

Use `realtime_threshold_samples` in [config/evaluation.yaml](../config/evaluation.yaml) to combine fast developer feedback with low-cost regression sweeps:

| Workflow | Dataset Size | Execution Mode | Typical Latency | Cost Discount |
|---|---:|---|---|---:|
| PR fast smoke check | `< 10` samples | Real-time | Seconds | 0% |
| Nightly / release golden eval | `>= 10` samples (e.g., 180–1,000) | Batch API | Minutes–24h | 30–50% |

---

## Configuration (`config/evaluation.yaml`)

```yaml
evaluation:
  batch_mode:
    enabled: true
    provider: openai               # 'openai' (50% off) or 'anthropic' (30% off)
    batch_size: 50                 # samples per JSONL batch file
    max_concurrent_batches: 10     # parallel submissions
    poll_interval_seconds: 60      # seconds between status polls
    max_poll_duration_seconds: 86400
    fallback_to_realtime: true     # auto-recover failed/expired samples
    realtime_threshold_samples: 10 # < 10 samples -> real-time
    track_savings: true
```

Credentials are never stored in YAML. Adapters read `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` from the environment at runtime and redact any secrets from error messages via `SecretDetector`.

---

## Usage Examples

### 1. Hybrid `EvalRunner` from YAML

```python
import asyncio
from product_value_system.evaluation import EvalRunner, BatchResult

async def realtime_judge(sample: dict) -> BatchResult:
    # Fast synchronous/async fallback for small PR checks or failed batch items
    ...

async def main() -> None:
    runner = EvalRunner.from_yaml(
        "config/evaluation.yaml",
        realtime_judge=realtime_judge,
    )
    summary = await runner.run_eval(samples, model="gpt-4-turbo")
    print(f"Mode: {summary.mode}")
    print(f"Mean score: {summary.mean_score:.3f} (pass rate: {summary.pass_rate:.1%})")
    print(
        f"Cost: ${summary.cost_summary.batch_cost_usd:.4f} "
        f"(saved ${summary.cost_summary.savings_usd:.4f} / "
        f"{summary.cost_summary.savings_pct:.1f}%)"
    )

asyncio.run(main())
```

### 2. Direct `BatchEvalRunner` with Progress Tracking

```python
from product_value_system.evaluation import BatchEvalRunner

runner = BatchEvalRunner(
    provider="openai",
    batch_size=50,
    poll_interval=60.0,
    max_concurrent_batches=10,
)
runner.poller.on_progress = lambda done, total: print(f"Completed {done}/{total} batches")
report = await runner.run_batch_eval_with_report(
    samples=golden_samples,
    judge_prompt_template=template,
    model="gpt-4-turbo",
)
```

---

## Rollback & Recovery

1. **Instant Rollback**: Set `evaluation.batch_mode.enabled: false` in [config/evaluation.yaml](../config/evaluation.yaml). All evaluations immediately run in real-time mode with no code changes.
2. **Automatic Partial Fallback**: With `fallback_to_realtime: true`, any sample whose batch job fails, expires, or returns an item error is automatically re-judged via `realtime_judge` and reflected accurately in `EvalRunSummary.mode == "batch_with_realtime_fallback"` and blended cost calculations.
3. **Cache Hygiene**: Generated JSONL batch payloads live under `.cache/eval_batches/` (ignored by Git via `.gitignore`) and can be removed safely between runs.

---

## Troubleshooting

- **Batch polling times out (`status == "expired"`)**: Increase `max_poll_duration_seconds` (up to `86400` for OpenAI's 24-hour window) or enable `fallback_to_realtime: true`.
- **Rate limits (`HTTP 429`) during batch upload**: Lower `max_concurrent_batches` (e.g., from `10` to `3`). Built-in exponential backoff automatically retries `429` and `5xx` responses.
- **Missing API key error**: Export `OPENAI_API_KEY` (for `provider: openai`) or `ANTHROPIC_API_KEY` (for `provider: anthropic`), or pass a mocked `BatchAdapter` in tests.
- **Score divergence between batch and real-time**: `BatchEvalRunner` enforces `temperature: 0.0` in generated JSONL payloads to maximize determinism across batch and real-time judge runs.

---

## Related Documentation

- [Cost Management](COST_MANAGEMENT.md)
- [Model Routing Guide](model-routing.md)
- [LLM Token Caching Guide](token-caching.md)
- [Golden Dataset Curation Guide](../golden_datasets/README.md)
- [Testing Guide](../tests/README.md)

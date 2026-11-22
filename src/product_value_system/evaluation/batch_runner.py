"""
Batch evaluation runner splitting datasets into JSONL batches, submitting concurrently
to OpenAI or Anthropic Batch APIs, polling for completion, tracking 30-50% cost savings,
and falling back to real-time judging on batch failure (EVO-027).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.evaluation.batch_adapters import (
    AnthropicBatchAdapter,
    AzureOpenAIBatchAdapter,
    BatchAdapter,
    BatchJob,
    BatchResult,
    GeminiBatchAdapter,
    OpenAIBatchAdapter,
)
from product_value_system.evaluation.batch_poller import BatchPoller

logger = logging.getLogger(__name__)

RealtimeFallbackFn = Callable[[dict[str, Any]], Awaitable[BatchResult] | BatchResult]

# Per-1M-token real-time baseline pricing (USD)
MODEL_REALTIME_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4-turbo-2024-04-09": (10.00, 30.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-opus-20240229": (15.00, 75.00),
    "claude-3-haiku-20240307": (0.25, 1.25),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-2.0-flash": (0.10, 0.40),
}

PROVIDER_BATCH_DISCOUNT: dict[str, float] = {
    "openai": 0.50,  # 50% off via OpenAI Batch API
    "anthropic": 0.30,  # 30% off via Anthropic Message Batches API
    "gemini": 0.50,  # 50% off via Google Gemini Batch API
    "azure_openai": 0.50,  # 50% off via Azure OpenAI Global Batch API
}


def estimate_batch_savings(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> tuple[float, float, float, float]:
    """Return ``(realtime_cost_usd, batch_cost_usd, savings_usd, savings_pct)``."""
    in_price, out_price = MODEL_REALTIME_PRICING.get(model, (10.00, 30.00))
    realtime_cost = (
        (input_tokens / 1_000_000.0) * in_price + (output_tokens / 1_000_000.0) * out_price
    )
    discount = PROVIDER_BATCH_DISCOUNT.get(provider.lower(), 0.50)
    batch_cost = realtime_cost * (1.0 - discount)
    savings = realtime_cost - batch_cost
    savings_pct = (savings / realtime_cost * 100.0) if realtime_cost > 0 else discount * 100.0
    return (
        round(realtime_cost, 6),
        round(batch_cost, 6),
        round(savings, 6),
        round(savings_pct, 2),
    )


@dataclass
class BatchCostSummary:
    """Cost and savings comparison between batch execution and real-time baseline."""

    provider: str
    model: str
    total_requests: int
    succeeded_requests: int
    failed_requests: int
    input_tokens: int
    output_tokens: int
    realtime_cost_usd: float
    batch_cost_usd: float
    savings_usd: float
    savings_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "total_requests": self.total_requests,
            "succeeded_requests": self.succeeded_requests,
            "failed_requests": self.failed_requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "realtime_cost_usd": round(self.realtime_cost_usd, 6),
            "batch_cost_usd": round(self.batch_cost_usd, 6),
            "savings_usd": round(self.savings_usd, 6),
            "savings_pct": round(self.savings_pct, 2),
        }


@dataclass
class BatchEvalReport:
    """Complete batch evaluation execution report with jobs, matched results & cost savings."""

    jobs: list[BatchJob]
    results: list[BatchResult]
    cost_summary: BatchCostSummary
    used_realtime_fallback: bool = False
    results_by_id: dict[str, BatchResult] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobs": [j.to_dict() for j in self.jobs],
            "results": [r.to_dict() for r in self.results],
            "cost_summary": self.cost_summary.to_dict(),
            "used_realtime_fallback": self.used_realtime_fallback,
        }


def _parse_single_result_line(result_json: dict[str, Any]) -> BatchResult:
    """Parse either an OpenAI Batch JSONL record or an Anthropic Message Batch JSONL record."""
    custom_id = str(
        result_json.get("custom_id")
        or result_json.get("key")
        or (result_json.get("metadata") or {}).get("key")
        or ""
    )
    error_obj = result_json.get("error")
    error_str = json.dumps(error_obj) if isinstance(error_obj, dict) else (
        str(error_obj) if error_obj else None
    )

    content = ""
    input_tokens = 0
    output_tokens = 0
    response_payload: dict[str, Any] = {}

    # OpenAI / Azure OpenAI or Gemini Batch response format
    if "response" in result_json and isinstance(result_json["response"], dict):
        response_payload = result_json["response"]
        body = response_payload.get("body", response_payload)
        if isinstance(body, dict) and "candidates" in body:
            # Gemini Batch JSONL format: {"key": ..., "response": {"candidates": [...]}}
            candidates = body.get("candidates") or []
            if candidates and isinstance(candidates[0], dict):
                parts = (candidates[0].get("content") or {}).get("parts") or []
                content = "".join(
                    str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p
                )
            usage_meta = body.get("usageMetadata") or {}
            input_tokens = int(usage_meta.get("promptTokenCount", 0) or 0)
            output_tokens = int(usage_meta.get("candidatesTokenCount", 0) or 0)
        elif isinstance(body, dict):
            choices = body.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                content = str(msg.get("content", ""))
            usage = body.get("usage") or {}
            input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
            output_tokens = int(
                usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
            )
    # Anthropic Batch format: {"custom_id": ..., "result": {"type": "succeeded", "message": {...}}}
    elif "result" in result_json and isinstance(result_json["result"], dict):
        res_block = result_json["result"]
        response_payload = res_block
        if res_block.get("type") == "errored":
            error_str = json.dumps(res_block.get("error", "Anthropic batch item errored"))
        else:
            message = res_block.get("message") or {}
            blocks = message.get("content") or []
            text_parts = [
                str(b.get("text", ""))
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            content = "\n".join(text_parts)
            usage = message.get("usage") or {}
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)

    return BatchResult(
        custom_id=custom_id,
        response=response_payload,
        error=error_str,
        content=content,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class BatchEvalRunner:
    """Orchestrate discounted batch LLM-as-judge evaluations with polling & real-time fallback."""

    def __init__(
        self,
        provider: str = "openai",
        api_key: str | None = None,
        batch_size: int = 50,
        poll_interval: float = 60.0,
        max_poll_duration: float = 86400.0,
        *,
        max_concurrent_batches: int = 10,
        cache_dir: Path | str = Path(".cache/eval_batches"),
        adapter: BatchAdapter | None = None,
        fallback_to_realtime: bool = True,
    ) -> None:
        self.provider = provider.strip().lower()
        self.batch_size = max(1, int(batch_size))
        self.poll_interval = float(poll_interval)
        self.max_poll_duration = float(max_poll_duration)
        self.max_concurrent_batches = max(1, int(max_concurrent_batches))
        self.cache_dir = Path(cache_dir)
        self.fallback_to_realtime = fallback_to_realtime

        if adapter is not None:
            self.adapter: BatchAdapter = adapter
        elif self.provider == "openai":
            key = api_key or os.getenv("OPENAI_API_KEY") or ""
            self.adapter = OpenAIBatchAdapter(key)
        elif self.provider == "anthropic":
            key = api_key or os.getenv("ANTHROPIC_API_KEY") or ""
            self.adapter = AnthropicBatchAdapter(key)
        elif self.provider == "gemini":
            key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
            self.adapter = GeminiBatchAdapter(key)
        elif self.provider in {"azure_openai", "azure"}:
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or ""
            key = api_key or os.getenv("AZURE_OPENAI_API_KEY") or ""
            self.adapter = AzureOpenAIBatchAdapter(endpoint=endpoint, api_key=key)
        else:
            raise ValueError(f"Unsupported batch provider: {provider!r}")

        self.poller = BatchPoller(
            adapter=self.adapter,
            poll_interval=self.poll_interval,
            max_poll_duration=self.max_poll_duration,
        )
        self.last_report: BatchEvalReport | None = None

    async def run_batch_eval(
        self,
        samples: list[dict[str, Any]],
        judge_prompt_template: str,
        model: str = "gpt-4-turbo",
        *,
        realtime_fallback_fn: RealtimeFallbackFn | None = None,
    ) -> list[BatchResult]:
        """Run batch evaluation on ``samples`` and return sample-ordered ``BatchResult``s."""
        report = await self.run_batch_eval_with_report(
            samples=samples,
            judge_prompt_template=judge_prompt_template,
            model=model,
            realtime_fallback_fn=realtime_fallback_fn,
        )
        return report.results

    async def run_batch_eval_with_report(
        self,
        samples: list[dict[str, Any]],
        judge_prompt_template: str,
        model: str = "gpt-4-turbo",
        *,
        realtime_fallback_fn: RealtimeFallbackFn | None = None,
    ) -> BatchEvalReport:
        """Execute full batch evaluation lifecycle and return jobs, results, and cost savings."""
        if not samples:
            empty_cost = BatchCostSummary(
                provider=self.provider,
                model=model,
                total_requests=0,
                succeeded_requests=0,
                failed_requests=0,
                input_tokens=0,
                output_tokens=0,
                realtime_cost_usd=0.0,
                batch_cost_usd=0.0,
                savings_usd=0.0,
                savings_pct=0.0,
            )
            report = BatchEvalReport(jobs=[], results=[], cost_summary=empty_cost)
            self.last_report = report
            return report

        batch_files = self._create_batch_files(samples, judge_prompt_template, model)
        sem = asyncio.Semaphore(self.max_concurrent_batches)

        async def _submit_one(path: Path) -> BatchJob:
            async with sem:
                try:
                    job = await self.adapter.submit_batch(path)
                    logger.info(
                        "Submitted batch job %s (%d requests)",
                        job.batch_id,
                        job.num_requests,
                    )
                    return job
                except Exception as exc:
                    logger.error("Batch submission failed for %s: %s", path.name, exc)
                    return BatchJob(
                        batch_id=f"failed-{path.stem}",
                        provider=self.provider,
                        num_requests=0,
                        status="failed",
                        created_at=datetime.now(UTC).isoformat(),
                        error=str(exc),
                    )

        batch_jobs = list(await asyncio.gather(*(_submit_one(p) for p in batch_files)))
        active_jobs = [j for j in batch_jobs if j.status != "failed"]
        if active_jobs:
            await self.poller.poll(active_jobs, raise_on_timeout=False)

        raw_results = await self._parse_batch_results(batch_jobs)
        by_id: dict[str, BatchResult] = {r.custom_id: r for r in raw_results}

        # Identify samples missing or errored for optional real-time fallback
        used_fallback = False
        fallback_in_tokens = 0
        fallback_out_tokens = 0
        if self.fallback_to_realtime and realtime_fallback_fn is not None:
            missing_samples = [
                s
                for s in samples
                if str(s["id"]) not in by_id or by_id[str(s["id"])].error is not None
            ]
            if missing_samples:
                used_fallback = True
                logger.warning(
                    "Falling back to real-time judging for %d sample(s).",
                    len(missing_samples),
                )
                for sample in missing_samples:
                    fb_raw = realtime_fallback_fn(sample)
                    fb_res = await fb_raw if inspect.isawaitable(fb_raw) else fb_raw
                    by_id[str(sample["id"])] = fb_res
                    fallback_in_tokens += fb_res.input_tokens
                    fallback_out_tokens += fb_res.output_tokens

        # Order results consistently to match original sample sequence
        ordered_results: list[BatchResult] = []
        for sample in samples:
            sid = str(sample["id"])
            if sid in by_id:
                ordered_results.append(by_id[sid])
            else:
                ordered_results.append(
                    BatchResult(
                        custom_id=sid,
                        response={},
                        error="Missing result from batch execution",
                    )
                )

        batch_in_tokens = sum(r.input_tokens for r in raw_results if not r.error)
        batch_out_tokens = sum(r.output_tokens for r in raw_results if not r.error)
        total_in = batch_in_tokens + fallback_in_tokens
        total_out = batch_out_tokens + fallback_out_tokens

        # Compute batch cost (discounted) + any fallback calls (full real-time price)
        rt_batch, discounted_batch, _, _ = estimate_batch_savings(
            self.provider, model, batch_in_tokens, batch_out_tokens
        )
        rt_fb, _, _, _ = estimate_batch_savings(
            self.provider, model, fallback_in_tokens, fallback_out_tokens
        )
        total_realtime_cost = round(rt_batch + rt_fb, 6)
        total_actual_cost = round(discounted_batch + rt_fb, 6)
        savings_usd = round(max(0.0, total_realtime_cost - total_actual_cost), 6)
        savings_pct = (
            round((savings_usd / total_realtime_cost) * 100.0, 2)
            if total_realtime_cost > 0
            else 0.0
        )

        # Annotate completed jobs with cost attribution
        completed_jobs = [j for j in batch_jobs if j.status == "completed"]
        if completed_jobs:
            share_cost = discounted_batch / len(completed_jobs)
            share_rt = rt_batch / len(completed_jobs)
            for j in completed_jobs:
                j.cost_usd = round(share_cost, 6)
                j.realtime_equivalent_cost_usd = round(share_rt, 6)

        succeeded = sum(1 for r in ordered_results if r.error is None)
        cost_summary = BatchCostSummary(
            provider=self.provider,
            model=model,
            total_requests=len(samples),
            succeeded_requests=succeeded,
            failed_requests=len(samples) - succeeded,
            input_tokens=total_in,
            output_tokens=total_out,
            realtime_cost_usd=total_realtime_cost,
            batch_cost_usd=total_actual_cost,
            savings_usd=savings_usd,
            savings_pct=savings_pct,
        )

        report = BatchEvalReport(
            jobs=batch_jobs,
            results=ordered_results,
            cost_summary=cost_summary,
            used_realtime_fallback=used_fallback,
            results_by_id={r.custom_id: r for r in ordered_results},
        )
        self.last_report = report
        return report

    def _create_batch_files(
        self,
        samples: list[dict[str, Any]],
        judge_prompt_template: str,
        model: str,
    ) -> list[Path]:
        """Split ``samples`` into provider-compatible JSONL batch files of size ``batch_size``."""
        effective_size = min(self.batch_size, self.adapter.max_requests_per_batch)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        batch_files: list[Path] = []

        for idx in range(0, len(samples), effective_size):
            chunk = samples[idx : idx + effective_size]
            batch_file = self.cache_dir / f"batch_{idx // effective_size:04d}.jsonl"
            lines: list[str] = []
            for sample in chunk:
                judge_prompt = judge_prompt_template.format(
                    input=sample.get("input", ""),
                    expected_output=sample.get("expected_output", ""),
                    actual_output=sample.get("actual_output", ""),
                )
                request = {
                    "custom_id": str(sample["id"]),
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "You are an evaluation judge."},
                            {"role": "user", "content": judge_prompt},
                        ],
                        "temperature": 0.0,
                    },
                }
                lines.append(json.dumps(request))
            batch_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            batch_files.append(batch_file)

        return batch_files

    async def _poll_batch_jobs(self, batch_jobs: list[BatchJob]) -> list[BatchJob]:
        """Poll batch jobs until completion or timeout."""
        return await self.poller.poll(batch_jobs)

    async def _parse_batch_results(self, batch_jobs: list[BatchJob]) -> list[BatchResult]:
        """Download and parse JSONL results for all completed batch jobs."""
        all_results: list[BatchResult] = []
        for job in batch_jobs:
            if job.status != "completed" or not job.result_file_id:
                logger.warning("Skipping incomplete batch %s (status=%s)", job.batch_id, job.status)
                continue
            try:
                raw_text = await self.adapter.download_result_file(job.result_file_id)
            except Exception as exc:
                logger.error("Failed downloading results for %s: %s", job.batch_id, exc)
                job.status = "failed"
                job.error = str(exc)
                continue

            for line in raw_text.splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                all_results.append(_parse_single_result_line(record))

        return all_results

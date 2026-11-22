"""
Hybrid EvalRunner supporting automatic real-time vs. discounted batch evaluation mode
based on dataset size and ``config/evaluation.yaml`` (EVO-027).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from product_value_system.evaluation.batch_adapters import BatchAdapter, BatchJob, BatchResult
from product_value_system.evaluation.batch_runner import (
    BatchCostSummary,
    BatchEvalRunner,
    estimate_batch_savings,
)

RealtimeJudgeFn = Callable[[dict[str, Any]], Awaitable[BatchResult] | BatchResult]

DEFAULT_JUDGE_TEMPLATE = (
    "Evaluate the actual output against the expected output for the input below.\n"
    "Input: {input}\n"
    "Expected: {expected_output}\n"
    "Actual: {actual_output}\n"
    'Respond with JSON: {{"score": 0.0-1.0, "passed": true/false, "reason": "..."}}'
)


def _extract_score_and_pass(result: BatchResult, passing_threshold: float) -> tuple[float, bool]:
    if result.error:
        return 0.0, False
    text = result.content.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            score = max(0.0, min(1.0, float(parsed.get("score", 0.0))))
            passed = bool(parsed.get("passed", score >= passing_threshold))
            return score, passed
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).debug(
            "Judge output for %s was non-JSON (%s); falling back to numeric extraction.",
            result.custom_id,
            exc,
        )
    # Fallback numeric extraction
    m = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
    score = float(m.group(1)) if m else (1.0 if text else 0.0)
    return score, score >= passing_threshold


@dataclass
class EvalRunSummary:
    """Unified evaluation report returned identically for batch and real-time modes."""

    mode: str  # 'realtime', 'batch', 'batch_with_realtime_fallback'
    sample_count: int
    passed_count: int
    pass_rate: float
    mean_score: float
    scores_by_id: dict[str, float]
    results: list[BatchResult]
    cost_summary: BatchCostSummary
    batch_jobs: list[BatchJob] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sample_count": self.sample_count,
            "passed_count": self.passed_count,
            "pass_rate": round(self.pass_rate, 4),
            "mean_score": round(self.mean_score, 4),
            "scores_by_id": {k: round(v, 4) for k, v in self.scores_by_id.items()},
            "cost_summary": self.cost_summary.to_dict(),
            "batch_jobs": [j.to_dict() for j in self.batch_jobs],
        }


class EvalRunner:
    """Evaluation runner transparently choosing real-time or discounted batch execution."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        batch_adapter: BatchAdapter | None = None,
        realtime_judge: RealtimeJudgeFn | None = None,
        passing_threshold: float = 0.7,
        cache_dir: Path | str = Path(".cache/eval_batches"),
    ) -> None:
        raw = config or {}
        eval_cfg = raw.get("evaluation", raw)
        bm = eval_cfg.get("batch_mode", {}) or {}

        self.batch_enabled: bool = bool(bm.get("enabled", False))
        self.provider: str = str(bm.get("provider", "openai")).lower()
        self.batch_size: int = int(bm.get("batch_size", 50))
        self.max_concurrent_batches: int = int(bm.get("max_concurrent_batches", 10))
        self.poll_interval: float = float(bm.get("poll_interval_seconds", 60.0))
        self.max_poll_duration: float = float(bm.get("max_poll_duration_seconds", 86400.0))
        self.fallback_to_realtime: bool = bool(bm.get("fallback_to_realtime", True))
        self.realtime_threshold: int = int(bm.get("realtime_threshold_samples", 10))
        self.track_savings: bool = bool(bm.get("track_savings", True))
        self.passing_threshold = float(passing_threshold)
        self.realtime_judge = realtime_judge
        self._batch_adapter = batch_adapter
        self._cache_dir = Path(cache_dir)

        self.batch_runner: BatchEvalRunner | None = None
        if self.batch_enabled and batch_adapter is not None:
            self.batch_runner = self._build_batch_runner()

    def _build_batch_runner(self) -> BatchEvalRunner:
        return BatchEvalRunner(
            provider=self.provider,
            batch_size=self.batch_size,
            poll_interval=self.poll_interval,
            max_poll_duration=self.max_poll_duration,
            max_concurrent_batches=self.max_concurrent_batches,
            cache_dir=self._cache_dir,
            adapter=self._batch_adapter,
            fallback_to_realtime=self.fallback_to_realtime,
        )

    @classmethod
    def from_yaml(
        cls,
        path: Path | str,
        *,
        batch_adapter: BatchAdapter | None = None,
        realtime_judge: RealtimeJudgeFn | None = None,
        passing_threshold: float = 0.7,
        cache_dir: Path | str = Path(".cache/eval_batches"),
    ) -> EvalRunner:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(
            data,
            batch_adapter=batch_adapter,
            realtime_judge=realtime_judge,
            passing_threshold=passing_threshold,
            cache_dir=cache_dir,
        )

    async def run_eval(
        self,
        samples: list[dict[str, Any]],
        *,
        judge_prompt_template: str = DEFAULT_JUDGE_TEMPLATE,
        model: str = "gpt-4-turbo",
    ) -> EvalRunSummary:
        """Run evaluation via batch mode when enabled & above threshold, else real-time."""
        use_batch = self.batch_enabled and len(samples) >= self.realtime_threshold

        if use_batch:
            if self.batch_runner is None:
                self.batch_runner = self._build_batch_runner()
            report = await self.batch_runner.run_batch_eval_with_report(
                samples=samples,
                judge_prompt_template=judge_prompt_template,
                model=model,
                realtime_fallback_fn=self.realtime_judge,
            )
            mode = "batch_with_realtime_fallback" if report.used_realtime_fallback else "batch"
            return self._summarize(
                mode=mode,
                results=report.results,
                cost_summary=report.cost_summary,
                batch_jobs=report.jobs,
            )

        rt_results = await self._run_realtime_eval(samples)
        in_tok = sum(r.input_tokens for r in rt_results)
        out_tok = sum(r.output_tokens for r in rt_results)
        rt_cost, _, _, _ = estimate_batch_savings(self.provider, model, in_tok, out_tok)
        cost_summary = BatchCostSummary(
            provider=self.provider,
            model=model,
            total_requests=len(samples),
            succeeded_requests=sum(1 for r in rt_results if r.error is None),
            failed_requests=sum(1 for r in rt_results if r.error is not None),
            input_tokens=in_tok,
            output_tokens=out_tok,
            realtime_cost_usd=rt_cost,
            batch_cost_usd=rt_cost,
            savings_usd=0.0,
            savings_pct=0.0,
        )
        return self._summarize(mode="realtime", results=rt_results, cost_summary=cost_summary)

    async def _run_realtime_eval(self, samples: list[dict[str, Any]]) -> list[BatchResult]:
        if self.realtime_judge is None:
            raise ValueError("realtime_judge callable is required for real-time evaluation mode.")

        async def _eval_one(sample: dict[str, Any]) -> BatchResult:
            assert self.realtime_judge is not None
            raw = self.realtime_judge(sample)
            return await raw if inspect.isawaitable(raw) else raw

        return list(await asyncio.gather(*(_eval_one(s) for s in samples)))

    def _summarize(
        self,
        *,
        mode: str,
        results: list[BatchResult],
        cost_summary: BatchCostSummary,
        batch_jobs: list[BatchJob] | None = None,
    ) -> EvalRunSummary:
        scores_by_id: dict[str, float] = {}
        passed_count = 0
        for res in results:
            score, passed = _extract_score_and_pass(res, self.passing_threshold)
            scores_by_id[res.custom_id] = score
            if passed:
                passed_count += 1

        n = len(results)
        mean_score = sum(scores_by_id.values()) / n if n > 0 else 0.0
        pass_rate = passed_count / n if n > 0 else 0.0
        return EvalRunSummary(
            mode=mode,
            sample_count=n,
            passed_count=passed_count,
            pass_rate=pass_rate,
            mean_score=mean_score,
            scores_by_id=scores_by_id,
            results=results,
            cost_summary=cost_summary,
            batch_jobs=list(batch_jobs or []),
        )

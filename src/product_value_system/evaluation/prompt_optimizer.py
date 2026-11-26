"""
DSPy-inspired Prompt Optimizer (BootstrapFewShot + Candidate Instruction Search)
evaluating prompt variants scientifically against golden datasets (EVO-P3-13).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from product_value_system.evaluation.prompt_registry import PromptTemplate

PromptRunner = Callable[[PromptTemplate, dict[str, Any]], Awaitable[str] | str]
MetricFn = Callable[[str, str, dict[str, Any]], Awaitable[float] | float]


@dataclass(frozen=True)
class LabeledExample:
    """Single golden training/validation example for prompt optimization."""

    example_id: str
    inputs: dict[str, Any]
    expected_output: str


@dataclass(frozen=True)
class CandidateTrial:
    """Evaluation summary for one candidate prompt configuration."""

    candidate_id: str
    version: str
    val_score: float
    demo_count: int
    instruction_summary: str


@dataclass(frozen=True)
class OptimizationResult:
    """Outcome of a DSPy-style BootstrapFewShot / candidate search run."""

    best_template: PromptTemplate
    baseline_score: float
    optimized_score: float
    improvement_delta: float
    selected_demo_ids: list[str]
    trials: list[CandidateTrial] = field(default_factory=list)


class PromptOptimizer:
    """
    Optimizes ``PromptTemplate`` instructions and bootstrapped few-shot demos
    against a golden validation split using an empirical metric function.
    """

    def __init__(
        self,
        metric_fn: MetricFn,
        *,
        min_demo_score: float = 0.80,
        max_bootstrapped_demos: int = 3,
    ) -> None:
        self.metric_fn = metric_fn
        self.min_demo_score = float(min_demo_score)
        self.max_bootstrapped_demos = max(1, int(max_bootstrapped_demos))

    async def _run_single(
        self,
        runner: PromptRunner,
        template: PromptTemplate,
        inputs: dict[str, Any],
    ) -> str:
        res = runner(template, inputs)
        return str(await res if inspect.isawaitable(res) else res)

    async def _score_single(
        self,
        predicted: str,
        expected: str,
        inputs: dict[str, Any],
    ) -> float:
        res = self.metric_fn(predicted, expected, inputs)
        val = await res if inspect.isawaitable(res) else res
        return max(0.0, min(1.0, float(val)))

    async def evaluate_template(
        self,
        template: PromptTemplate,
        dataset: Sequence[LabeledExample],
        runner: PromptRunner,
    ) -> float:
        """Compute mean metric score of ``template`` across ``dataset``."""
        if not dataset:
            return 0.0
        scores: list[float] = []
        for ex in dataset:
            predicted = await self._run_single(runner, template, ex.inputs)
            scores.append(await self._score_single(predicted, ex.expected_output, ex.inputs))
        return sum(scores) / len(scores)

    async def bootstrap_demos(
        self,
        template: PromptTemplate,
        train_examples: Sequence[LabeledExample],
        runner: PromptRunner,
    ) -> list[tuple[LabeledExample, str, float]]:
        """Bootstrap high-scoring few-shot traces from ``train_examples``."""
        scored: list[tuple[LabeledExample, str, float]] = []
        for ex in train_examples:
            predicted = await self._run_single(runner, template, ex.inputs)
            score = await self._score_single(predicted, ex.expected_output, ex.inputs)
            if score >= self.min_demo_score:
                scored.append((ex, predicted, score))
        scored.sort(key=lambda item: item[2], reverse=True)
        return scored[: self.max_bootstrapped_demos]

    @staticmethod
    def _augment_template(
        base: PromptTemplate,
        *,
        version_suffix: str,
        system_prompt: str,
        demos: Sequence[tuple[LabeledExample, str, float]],
    ) -> PromptTemplate:
        if not demos:
            return PromptTemplate(
                id=base.id,
                version=f"{base.version}-{version_suffix}",
                agent=base.agent,
                system_prompt=system_prompt,
                user_template=base.user_template,
                description=base.description,
                metadata={**base.metadata, "optimized": True, "demo_count": 0},
            )

        demo_blocks: list[str] = []
        for idx, (ex, trace_output, _score) in enumerate(demos, start=1):
            inputs_fmt = ", ".join(f"{k}={v}" for k, v in sorted(ex.inputs.items()))
            demo_blocks.append(
                f"Example {idx}:\nInput: {inputs_fmt}\nVerified Output: {trace_output}"
            )
        augmented_system = (
            f"{system_prompt}\n\n## Verified Few-Shot Demonstrations\n"
            + "\n\n".join(demo_blocks)
        )
        return PromptTemplate(
            id=base.id,
            version=f"{base.version}-{version_suffix}",
            agent=base.agent,
            system_prompt=augmented_system,
            user_template=base.user_template,
            description=base.description,
            metadata={
                **base.metadata,
                "optimized": True,
                "demo_count": len(demos),
                "demo_ids": [ex.example_id for ex, _, _ in demos],
            },
        )

    async def optimize(
        self,
        base_template: PromptTemplate,
        train_examples: Sequence[LabeledExample],
        val_examples: Sequence[LabeledExample],
        *,
        runner: PromptRunner,
        candidate_instructions: Sequence[str] | None = None,
    ) -> OptimizationResult:
        """
        Run BootstrapFewShot + candidate instruction search and select the
        highest-scoring prompt on ``val_examples``.
        """
        baseline_score = await self.evaluate_template(base_template, val_examples, runner)
        trials: list[CandidateTrial] = [
            CandidateTrial(
                candidate_id="baseline",
                version=base_template.version,
                val_score=round(baseline_score, 4),
                demo_count=0,
                instruction_summary=base_template.system_prompt[:80],
            )
        ]

        best_template = base_template
        best_score = baseline_score
        best_demo_ids: list[str] = []

        bootstrapped = await self.bootstrap_demos(base_template, train_examples, runner)

        instructions = [base_template.system_prompt]
        if candidate_instructions:
            for instr in candidate_instructions:
                if instr.strip() and instr != base_template.system_prompt:
                    instructions.append(instr.strip())

        candidate_idx = 0
        for instr in instructions:
            # Evaluate both zero-shot instruction candidate and few-shot bootstrapped variant
            demo_configs: list[tuple[str, list[tuple[LabeledExample, str, float]]]] = []
            if instr != base_template.system_prompt:
                demo_configs.append(("zeroshot", []))
            if bootstrapped:
                demo_configs.append(("fewshot", bootstrapped))

            for label, demos in demo_configs:
                candidate_idx += 1
                cand = self._augment_template(
                    base_template,
                    version_suffix=f"opt{candidate_idx}",
                    system_prompt=instr,
                    demos=demos,
                )
                score = await self.evaluate_template(cand, val_examples, runner)
                trials.append(
                    CandidateTrial(
                        candidate_id=f"cand-{candidate_idx}-{label}",
                        version=cand.version,
                        val_score=round(score, 4),
                        demo_count=len(demos),
                        instruction_summary=instr[:80],
                    )
                )
                if score > best_score:
                    best_score = score
                    best_template = cand
                    best_demo_ids = [ex.example_id for ex, _, _ in demos]

        return OptimizationResult(
            best_template=best_template,
            baseline_score=round(baseline_score, 4),
            optimized_score=round(best_score, 4),
            improvement_delta=round(best_score - baseline_score, 4),
            selected_demo_ids=best_demo_ids,
            trials=trials,
        )

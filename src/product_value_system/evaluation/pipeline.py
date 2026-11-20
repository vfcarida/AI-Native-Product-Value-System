"""
Evaluation pipeline (runs multiple judges).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from product_value_system.evaluation.golden_dataset import GoldenSample as LegacyGoldenSample
from product_value_system.evaluation.golden_dataset_loader import (
    GoldenDataset,
)
from product_value_system.evaluation.golden_dataset_loader import (
    GoldenSample as VersionedGoldenSample,
)
from product_value_system.evaluation.judge import Judge, JudgeResult


@dataclass
class EvaluationResult:
    """Result of evaluation pipeline."""

    artifact_id: str
    artifact_type: str
    judge_results: list[JudgeResult]
    overall_score: float
    passed: bool

    @property
    def failed_judges(self) -> list[JudgeResult]:
        """Get judges that failed."""
        return [r for r in self.judge_results if not r.passed]

    def to_evidence_artifact(self) -> dict[str, Any]:
        """Convert evaluation result to a schema-valid evidence artifact."""
        safe_id = self.artifact_id.lower().replace("_", "-")
        if not safe_id.startswith("evidence-"):
            safe_id = f"evidence-eval-{safe_id}"

        rationales = "; ".join(
            f"{r.judge_name}: {r.rationale} (score={r.score:.2f})"
            for r in self.judge_results
        )

        return {
            "id": safe_id,
            "version": 1,
            "created": datetime.now(UTC).isoformat(),
            "type": "evaluation",
            "subject_id": self.artifact_id,
            "verdict": "pass" if self.passed else "fail",
            "score": round(max(0.0, min(1.0, self.overall_score)), 4),
            "rationale": rationales,
            "artifacts": [
                {
                    "name": f"{self.artifact_type}_evaluation_report",
                    "path": f"artifacts/evaluations/{safe_id}.json",
                    "mime_type": "application/json",
                }
            ],
        }


class EvaluationPipeline:
    """
    Evaluation pipeline runs multiple judges in parallel.
    """

    def __init__(
        self,
        judges: list[Judge],
        passing_threshold: float = 0.7,
    ) -> None:
        """
        Initialize evaluation pipeline.

        Args:
            judges: List of judges to run
            passing_threshold: Overall score threshold (0.0 to 1.0)
        """
        self.judges = judges
        self.passing_threshold = passing_threshold

    async def evaluate(
        self,
        artifact: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        """
        Evaluate artifact with all judges.

        Args:
            artifact: Artifact to evaluate
            context: Optional context for judges

        Returns:
            EvaluationResult with all judge results
        """
        tasks = [judge.evaluate(artifact, context) for judge in self.judges]
        judge_results = await asyncio.gather(*tasks)

        overall_score = (
            sum(r.score for r in judge_results) / len(judge_results)
            if judge_results
            else 0.0
        )

        deterministic_passed = all(
            r.passed
            for r in judge_results
            if r.judge_name in {"schema-validation", "python-syntax"}
        )

        passed = overall_score >= self.passing_threshold and deterministic_passed

        return EvaluationResult(
            artifact_id=str(artifact.get("id", "unknown")),
            artifact_type=(
                str(context.get("artifact_type", "unknown")) if context else "unknown"
            ),
            judge_results=list(judge_results),
            overall_score=overall_score,
            passed=passed,
        )

    async def evaluate_golden_dataset(
        self,
        samples: Sequence[LegacyGoldenSample | VersionedGoldenSample] | GoldenDataset,
        artifact_type: str,
    ) -> list[EvaluationResult]:
        """Evaluate multiple golden samples (supports legacy lists and ``GoldenDataset``)."""
        sample_list = samples.samples if isinstance(samples, GoldenDataset) else samples
        results: list[EvaluationResult] = []

        for sample in sample_list:
            ctx: dict[str, Any] = {
                "artifact_type": artifact_type,
                "expected_output": sample.expected_output,
                "expected": sample.expected_output,
                "sample_id": sample.id,
            }
            if isinstance(sample, VersionedGoldenSample):
                ctx["rubric"] = sample.evaluation_rubric
                ctx["pass_threshold"] = sample.pass_threshold
                ctx["category"] = sample.category

            result = await self.evaluate(
                sample.input_artifact,
                context=ctx,
            )
            results.append(result)

        return results

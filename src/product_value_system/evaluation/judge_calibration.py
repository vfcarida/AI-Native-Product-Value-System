"""
LLM-as-a-Judge Human Calibration & Positional Bias Verification Suite (EVO-P3-13).

Provides:
- ``compute_cohens_kappa``: Inter-rater reliability statistic between human
  gold labels and AI judge binary pass/fail verdicts (target >= 0.80).
- ``calibrate_judge``: Evaluates any ``Judge`` against human-annotated samples,
  computing Cohen's Kappa, exact agreement, and Mean Absolute Error (MAE).
- ``measure_positional_bias``: Detects pairwise judge position-swap bias by
  comparing verdicts on ``(A, B)`` vs. swapped ``(B, A)``.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from product_value_system.evaluation.judge import Judge

PairwiseJudgeFn = Callable[[str, str], Awaitable[str] | str]


@dataclass(frozen=True)
class CalibrationSample:
    """Single human-labeled ground-truth sample for judge calibration."""

    sample_id: str
    artifact: dict[str, Any]
    human_passed: bool
    human_score: float
    context: dict[str, Any] | None = None


@dataclass(frozen=True)
class CalibrationReport:
    """Statistical calibration report comparing an AI Judge against human labels."""

    judge_name: str
    sample_count: int
    cohens_kappa: float
    exact_agreement: float
    mae: float
    kappa_threshold: float
    passed_calibration: bool
    per_sample: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "judge_name": self.judge_name,
            "sample_count": self.sample_count,
            "cohens_kappa": round(self.cohens_kappa, 4),
            "exact_agreement": round(self.exact_agreement, 4),
            "mae": round(self.mae, 4),
            "kappa_threshold": self.kappa_threshold,
            "passed_calibration": self.passed_calibration,
            "per_sample": list(self.per_sample),
        }


@dataclass(frozen=True)
class PairwiseComparisonSample:
    """Pair of candidate outputs used to test judge position-swap bias."""

    pair_id: str
    option_a: str
    option_b: str


@dataclass(frozen=True)
class PositionalBiasReport:
    """Positional invariance report across ``(A, B)`` vs. ``(B, A)`` comparisons."""

    total_pairs: int
    inconsistent_pairs: int
    positional_bias_rate: float
    max_allowed_bias_rate: float
    passed: bool
    details: list[dict[str, Any]] = field(default_factory=list)


def compute_cohens_kappa(
    y_human: Sequence[bool],
    y_judge: Sequence[bool],
) -> float:
    """Compute Cohen's Kappa inter-rater reliability in ``[-1.0, 1.0]``."""
    if len(y_human) != len(y_judge) or not y_human:
        raise ValueError("y_human and y_judge must be non-empty sequences of equal length")

    n = len(y_human)
    tp = sum(1 for h, j in zip(y_human, y_judge, strict=True) if h and j)
    tn = sum(1 for h, j in zip(y_human, y_judge, strict=True) if (not h) and (not j))
    fp = sum(1 for h, j in zip(y_human, y_judge, strict=True) if (not h) and j)
    fn = sum(1 for h, j in zip(y_human, y_judge, strict=True) if h and (not j))

    po = (tp + tn) / n
    p_human_pos = (tp + fn) / n
    p_judge_pos = (tp + fp) / n
    p_human_neg = (tn + fp) / n
    p_judge_neg = (tn + fn) / n
    pe = (p_human_pos * p_judge_pos) + (p_human_neg * p_judge_neg)

    if abs(1.0 - pe) < 1e-12:
        return 1.0 if abs(po - 1.0) < 1e-12 else 0.0
    return (po - pe) / (1.0 - pe)


async def calibrate_judge(
    judge: Judge,
    samples: Sequence[CalibrationSample],
    *,
    kappa_threshold: float = 0.80,
) -> CalibrationReport:
    """Evaluate a ``Judge`` against human-annotated ground truth samples."""
    if not samples:
        raise ValueError("samples must not be empty")

    human_labels: list[bool] = []
    judge_labels: list[bool] = []
    abs_errors: list[float] = []
    details: list[dict[str, Any]] = []

    for sample in samples:
        res = await judge.evaluate(sample.artifact, sample.context)
        human_labels.append(bool(sample.human_passed))
        judge_labels.append(bool(res.passed))
        err = abs(float(sample.human_score) - float(res.score))
        abs_errors.append(err)
        details.append(
            {
                "sample_id": sample.sample_id,
                "human_passed": sample.human_passed,
                "judge_passed": res.passed,
                "human_score": sample.human_score,
                "judge_score": res.score,
                "abs_error": round(err, 4),
            }
        )

    kappa = compute_cohens_kappa(human_labels, judge_labels)
    agreement = sum(
        1 for h, j in zip(human_labels, judge_labels, strict=True) if h == j
    ) / len(samples)
    mae = sum(abs_errors) / len(samples)

    return CalibrationReport(
        judge_name=judge.name,
        sample_count=len(samples),
        cohens_kappa=kappa,
        exact_agreement=agreement,
        mae=mae,
        kappa_threshold=kappa_threshold,
        passed_calibration=kappa >= kappa_threshold,
        per_sample=details,
    )


async def measure_positional_bias(
    pairwise_judge: PairwiseJudgeFn,
    pairs: Sequence[PairwiseComparisonSample],
    *,
    max_bias_rate: float = 0.10,
) -> PositionalBiasReport:
    """
    Detect positional bias by evaluating ``(A, B)`` and swapped ``(B, A)``.

    A position-invariant judge returning ``"A"`` on ``(option_a, option_b)``
    MUST return ``"B"`` on ``(option_b, option_a)`` (and ``"TIE"`` -> ``"TIE"``).
    """
    if not pairs:
        raise ValueError("pairs must not be empty")

    async def _invoke(first: str, second: str) -> str:
        out = pairwise_judge(first, second)
        resolved = await out if inspect.isawaitable(out) else out
        return str(resolved).strip().upper()

    inconsistent = 0
    details: list[dict[str, Any]] = []
    expected_swap = {"A": "B", "B": "A", "TIE": "TIE"}

    for pair in pairs:
        forward = await _invoke(pair.option_a, pair.option_b)
        swapped = await _invoke(pair.option_b, pair.option_a)
        consistent = expected_swap.get(forward) == swapped
        if not consistent:
            inconsistent += 1
        details.append(
            {
                "pair_id": pair.pair_id,
                "forward_verdict": forward,
                "swapped_verdict": swapped,
                "consistent": consistent,
            }
        )

    bias_rate = inconsistent / len(pairs)
    return PositionalBiasReport(
        total_pairs=len(pairs),
        inconsistent_pairs=inconsistent,
        positional_bias_rate=bias_rate,
        max_allowed_bias_rate=max_bias_rate,
        passed=bias_rate <= max_bias_rate,
        details=details,
    )

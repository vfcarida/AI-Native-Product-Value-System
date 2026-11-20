"""
Data models for Experiment Design and A/B Testing framework (EVO-016).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ExperimentStatus(str, Enum):
    """Lifecycle states of an online controlled experiment."""

    DRAFT = "draft"
    RUNNING = "running"
    STOPPED_EARLY = "stopped_early"
    COMPLETED = "completed"
    ABORTED = "aborted"


class Variant(str, Enum):
    """Experiment arm identifiers."""

    CONTROL = "control"
    TREATMENT = "treatment"


class StatisticalTestType(str, Enum):
    """Supported statistical inference methods."""

    TTEST = "ttest"
    CHI_SQUARE = "chi_square"
    BAYESIAN = "bayesian"


def _parse_datetime(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass
class ExperimentHypothesis:
    """Statistical hypothesis contract for an A/B experiment."""

    baseline_value: float
    expected_value: float
    minimum_detectable_effect: float
    confidence_level: float = 0.95
    statistical_power: float = 0.80
    primary_metric: str = "primary_metric"
    null_hypothesis: str = ""
    alternative_hypothesis: str = ""
    metric_type: str = "continuous"  # "continuous" or "proportion"

    def __post_init__(self) -> None:
        if not 0.5 < self.confidence_level < 1.0:
            raise ValueError(
                f"confidence_level must be in (0.5, 1.0), got {self.confidence_level}"
            )
        if not 0.5 < self.statistical_power < 1.0:
            raise ValueError(
                f"statistical_power must be in (0.5, 1.0), got {self.statistical_power}"
            )
        if self.minimum_detectable_effect <= 0.0:
            raise ValueError(
                "minimum_detectable_effect must be positive, "
                f"got {self.minimum_detectable_effect}"
            )
        if not self.null_hypothesis:
            self.null_hypothesis = f"No difference in {self.primary_metric} between variants"
        if not self.alternative_hypothesis:
            self.alternative_hypothesis = (
                f"Treatment changes {self.primary_metric} from "
                f"{self.baseline_value} to {self.expected_value}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize hypothesis to JSON/YAML dictionary."""
        return {
            "null_hypothesis": self.null_hypothesis,
            "alternative_hypothesis": self.alternative_hypothesis,
            "primary_metric": self.primary_metric,
            "baseline_value": float(self.baseline_value),
            "expected_value": float(self.expected_value),
            "minimum_detectable_effect": round(float(self.minimum_detectable_effect), 6),
            "confidence_level": float(self.confidence_level),
            "statistical_power": float(self.statistical_power),
            "metric_type": self.metric_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentHypothesis:
        """Deserialize hypothesis from dictionary."""
        return cls(
            null_hypothesis=str(data.get("null_hypothesis", "")),
            alternative_hypothesis=str(data.get("alternative_hypothesis", "")),
            primary_metric=str(data.get("primary_metric", "primary_metric")),
            baseline_value=float(data["baseline_value"]),
            expected_value=float(data["expected_value"]),
            minimum_detectable_effect=float(data["minimum_detectable_effect"]),
            confidence_level=float(data.get("confidence_level", 0.95)),
            statistical_power=float(data.get("statistical_power", 0.80)),
            metric_type=str(data.get("metric_type", "continuous")),
        )


@dataclass
class GuardrailMetric:
    """Metric monitored during experiments to detect and halt unintended harm."""

    name: str
    threshold: float
    direction: str  # "decrease" or "increase" is harmful
    description: str = ""

    def __post_init__(self) -> None:
        normalized = self.direction.strip().lower()
        if normalized == "below":
            normalized = "decrease"
        elif normalized == "above":
            normalized = "increase"
        if normalized not in {"decrease", "increase"}:
            raise ValueError(f"direction must be 'decrease' or 'increase', got {self.direction!r}")
        self.direction = normalized

    def to_dict(self) -> dict[str, Any]:
        """Serialize guardrail metric to dictionary."""
        return {
            "name": self.name,
            "threshold": float(self.threshold),
            "direction": self.direction,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GuardrailMetric:
        """Deserialize guardrail metric from dictionary."""
        return cls(
            name=str(data["name"]),
            threshold=float(data["threshold"]),
            direction=str(data["direction"]),
            description=str(data.get("description", "")),
        )


@dataclass
class ExperimentDesign:
    """Complete experiment blueprint produced from an outcome hypothesis."""

    id: str
    hypothesis: ExperimentHypothesis
    variants: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "control": {"description": "Current baseline experience"},
            "treatment": {"description": "Proposed experimental experience"},
        }
    )
    sample_size_per_variant: int = 1000
    duration_days: int = 14
    guardrails: list[GuardrailMetric] = field(default_factory=list)
    stopping_criteria: dict[str, Any] = field(
        default_factory=lambda: {
            "early_stop_enabled": True,
            "early_stop_alpha": 0.01,
            "max_duration_days": 14,
            "guardrail_violation_action": "abort",
        }
    )
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    outcome_id: str | None = None
    test_type: StatisticalTestType = StatisticalTestType.TTEST

    def __post_init__(self) -> None:
        if isinstance(self.test_type, str):
            self.test_type = StatisticalTestType(self.test_type)

    def to_dict(self) -> dict[str, Any]:
        """Serialize experiment design to schema-valid JSON/YAML dictionary."""
        return {
            "id": self.id,
            "outcome_id": self.outcome_id,
            "test_type": self.test_type.value,
            "hypothesis": self.hypothesis.to_dict(),
            "variants": dict(self.variants),
            "sample_size_per_variant": int(self.sample_size_per_variant),
            "duration_days": int(self.duration_days),
            "guardrails": [g.to_dict() for g in self.guardrails],
            "stopping_criteria": dict(self.stopping_criteria),
            "created_at": self.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExperimentDesign:
        """Deserialize experiment design from dictionary."""
        return cls(
            id=str(data["id"]),
            outcome_id=data.get("outcome_id"),
            test_type=StatisticalTestType(data.get("test_type", StatisticalTestType.TTEST.value)),
            hypothesis=ExperimentHypothesis.from_dict(data["hypothesis"]),
            variants=dict(data.get("variants", {})),
            sample_size_per_variant=int(data["sample_size_per_variant"]),
            duration_days=int(data["duration_days"]),
            guardrails=[GuardrailMetric.from_dict(g) for g in data.get("guardrails", [])],
            stopping_criteria=dict(data.get("stopping_criteria", {})),
            created_at=_parse_datetime(data.get("created_at")),
        )


@dataclass
class ExperimentResult:
    """Statistical and practical evaluation result for an A/B experiment."""

    experiment_id: str
    status: ExperimentStatus

    # Sample summary
    control_samples: int
    treatment_samples: int
    control_mean: float
    treatment_mean: float
    control_stddev: float
    treatment_stddev: float

    # Statistical inference
    lift: float
    confidence_interval: tuple[float, float]
    p_value: float
    statistically_significant: bool

    # Practical significance
    minimum_detectable_effect: float
    practically_significant: bool

    # Decision
    recommendation: str  # "adopt", "reject", "continue"
    reasoning: str

    # Guardrails
    guardrail_violations: list[dict[str, Any]] = field(default_factory=list)

    # Optional test details
    test_type: str = "ttest"
    bayesian_prob_treatment_beats_control: float | None = None
    bayesian_credible_interval: tuple[float, float] | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize experiment result to dictionary."""
        payload: dict[str, Any] = {
            "experiment_id": self.experiment_id,
            "status": self.status.value,
            "test_type": self.test_type,
            "control_samples": self.control_samples,
            "treatment_samples": self.treatment_samples,
            "control_mean": round(self.control_mean, 6),
            "treatment_mean": round(self.treatment_mean, 6),
            "control_stddev": round(self.control_stddev, 6),
            "treatment_stddev": round(self.treatment_stddev, 6),
            "lift": round(self.lift, 6),
            "confidence_interval": [
                round(self.confidence_interval[0], 6),
                round(self.confidence_interval[1], 6),
            ],
            "p_value": round(self.p_value, 6),
            "statistically_significant": self.statistically_significant,
            "minimum_detectable_effect": round(self.minimum_detectable_effect, 6),
            "practically_significant": self.practically_significant,
            "recommendation": self.recommendation,
            "reasoning": self.reasoning,
            "guardrail_violations": list(self.guardrail_violations),
            "completed_at": (
                self.completed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.completed_at is not None
                else None
            ),
        }
        if self.bayesian_prob_treatment_beats_control is not None:
            payload["bayesian_prob_treatment_beats_control"] = round(
                self.bayesian_prob_treatment_beats_control, 6
            )
        if self.bayesian_credible_interval is not None:
            payload["bayesian_credible_interval"] = [
                round(self.bayesian_credible_interval[0], 6),
                round(self.bayesian_credible_interval[1], 6),
            ]
        return payload

    def format_report(self) -> str:
        """Render a concise human-readable analysis report."""
        ci_lo, ci_hi = self.confidence_interval
        lines = [
            f"Experiment Report: {self.experiment_id} ({self.test_type})",
            "=" * 64,
            f"Status:          {self.status.value}",
            f"Control:         n={self.control_samples}, mean={self.control_mean:.4f} "
            f"(sd={self.control_stddev:.4f})",
            f"Treatment:       n={self.treatment_samples}, mean={self.treatment_mean:.4f} "
            f"(sd={self.treatment_stddev:.4f})",
            f"Relative Lift:   {self.lift:+.2%} (95% CI: [{ci_lo:+.2%}, {ci_hi:+.2%}])",
            f"p-value:         {self.p_value:.4f} "
            f"(statistically_significant={self.statistically_significant})",
            f"MDE Threshold:   {self.minimum_detectable_effect:.2%} "
            f"(practically_significant={self.practically_significant})",
        ]
        if self.bayesian_prob_treatment_beats_control is not None:
            lines.append(
                f"Bayesian P(T>C): {self.bayesian_prob_treatment_beats_control:.2%}"
            )
        if self.guardrail_violations:
            lines.append(f"Guardrails:      {len(self.guardrail_violations)} VIOLATION(S)")
            for v in self.guardrail_violations:
                lines.append(
                    f"  - {v['guardrail']}: actual={v['actual']:+.2%} "
                    f"(threshold={v['threshold']:+.2%})"
                )
        else:
            lines.append("Guardrails:      All passing")
        lines.extend(
            [
                "-" * 64,
                f"Recommendation:  {self.recommendation.upper()} — {self.reasoning}",
                "=" * 64,
            ]
        )
        return "\n".join(lines)

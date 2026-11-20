"""
Service Level Indicator (SLI) definitions for traditional and AI-native metrics (EVO-014).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SLIType(str, Enum):
    """Supported traditional and AI-specific SLI categories."""

    # 5 Traditional SLIs
    LATENCY = "latency"  # Response time (p50, p95, p99)
    ERROR_RATE = "error_rate"  # Failed requests / total requests
    AVAILABILITY = "availability"  # Successful requests / total requests
    THROUGHPUT = "throughput"  # Requests / outcomes completed per minute
    SATURATION = "saturation"  # Concurrent active runs / capacity ratio

    # 3 AI-Specific SLIs (+ Safety)
    QUALITY_SCORE = "quality_score"  # Evaluation pass rate (0-1)
    COST = "cost"  # Average USD cost per outcome
    HALLUCINATION = "hallucination"  # 1 - (hallucinated facts / total facts)
    SAFETY = "safety"  # 1 - (policy violations / total executions)


@dataclass(frozen=True)
class SLI:
    """Service Level Indicator: quantitative measure of service behavior."""

    name: str
    type: SLIType
    description: str
    measurement_method: str
    aggregation_window: str = "30d"
    unit: str = "ratio"
    higher_is_better: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize SLI definition to dictionary."""
        return {
            "name": self.name,
            "type": self.type.value,
            "description": self.description,
            "measurement_method": self.measurement_method,
            "aggregation_window": self.aggregation_window,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
        }


# 5 Traditional SLIs
AVAILABILITY_SLI = SLI(
    name="availability",
    type=SLIType.AVAILABILITY,
    description="Percentage of agent/value-stream requests completing without unhandled errors",
    measurement_method=(
        "sum(rate(pvs_requests_total{status='success'}[30d])) / "
        "sum(rate(pvs_requests_total[30d]))"
    ),
    aggregation_window="30d",
    unit="ratio",
    higher_is_better=True,
)

LATENCY_P95_SLI = SLI(
    name="latency_p95",
    type=SLIType.LATENCY,
    description="95th percentile agent model invocation latency in seconds",
    measurement_method=(
        "histogram_quantile(0.95, sum(rate(pvs_latency_seconds_bucket[7d])) by (le))"
    ),
    aggregation_window="7d",
    unit="seconds",
    higher_is_better=False,
)

ERROR_RATE_SLI = SLI(
    name="error_rate",
    type=SLIType.ERROR_RATE,
    description="Proportion of requests terminating in an error",
    measurement_method="sum(rate(pvs_errors_total[7d])) / sum(rate(pvs_requests_total[7d]))",
    aggregation_window="7d",
    unit="ratio",
    higher_is_better=False,
)

THROUGHPUT_SLI = SLI(
    name="throughput",
    type=SLIType.THROUGHPUT,
    description="Completed agent executions per minute",
    measurement_method="sum(rate(pvs_agent_executions_total{status='success'}[1h])) * 60",
    aggregation_window="1h",
    unit="executions/min",
    higher_is_better=True,
)

SATURATION_SLI = SLI(
    name="saturation",
    type=SLIType.SATURATION,
    description="Fraction of concurrent worker slots actively executing runs",
    measurement_method="pvs_active_runs / max_concurrent_runs",
    aggregation_window="1h",
    unit="ratio",
    higher_is_better=False,
)

# 3 AI-Specific SLIs (+ Safety)
QUALITY_SCORE_SLI = SLI(
    name="quality_score",
    type=SLIType.QUALITY_SCORE,
    description="Percentage of evaluated agent outputs meeting quality gate threshold",
    measurement_method=(
        "sum(rate(pvs_eval_executions_total{passed='true'}[7d])) / "
        "sum(rate(pvs_eval_executions_total[7d]))"
    ),
    aggregation_window="7d",
    unit="ratio",
    higher_is_better=True,
)

COST_PER_OUTCOME_SLI = SLI(
    name="cost_per_outcome",
    type=SLIType.COST,
    description="Average LLM + agent USD cost incurred per completed value-stream outcome",
    measurement_method=(
        "sum(increase(pvs_cost_usd_total[7d])) / "
        "sum(increase(pvs_agent_executions_total{status='success'}[7d]))"
    ),
    aggregation_window="7d",
    unit="USD",
    higher_is_better=False,
)

HALLUCINATION_FREE_SLI = SLI(
    name="hallucination_free_rate",
    type=SLIType.HALLUCINATION,
    description="1 - (hallucinated facts / total verified claims) across evaluated runs",
    measurement_method="1 - avg_over_time(pvs_hallucination_rate[7d])",
    aggregation_window="7d",
    unit="ratio",
    higher_is_better=True,
)

SAFETY_SLI = SLI(
    name="safety_compliance",
    type=SLIType.SAFETY,
    description="1 - (policy violations / total agent executions)",
    measurement_method=(
        "1 - (sum(rate(pvs_policy_violations_total[7d])) / "
        "sum(rate(pvs_agent_executions_total[7d])))"
    ),
    aggregation_window="7d",
    unit="ratio",
    higher_is_better=True,
)

TRADITIONAL_SLIS: dict[str, SLI] = {
    AVAILABILITY_SLI.name: AVAILABILITY_SLI,
    LATENCY_P95_SLI.name: LATENCY_P95_SLI,
    ERROR_RATE_SLI.name: ERROR_RATE_SLI,
    THROUGHPUT_SLI.name: THROUGHPUT_SLI,
    SATURATION_SLI.name: SATURATION_SLI,
}

AI_SLIS: dict[str, SLI] = {
    QUALITY_SCORE_SLI.name: QUALITY_SCORE_SLI,
    COST_PER_OUTCOME_SLI.name: COST_PER_OUTCOME_SLI,
    HALLUCINATION_FREE_SLI.name: HALLUCINATION_FREE_SLI,
    SAFETY_SLI.name: SAFETY_SLI,
}

DEFAULT_SLIS: dict[str, SLI] = {**TRADITIONAL_SLIS, **AI_SLIS}

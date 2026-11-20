"""
Experiment Design Agent transforming outcome hypotheses into statistically rigorous
A/B experiment designs (EVO-016).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from product_value_system.agents.agent import AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.experiment.models import (
    ExperimentDesign,
    ExperimentHypothesis,
    GuardrailMetric,
    StatisticalTestType,
)
from product_value_system.experiment.power_analysis import PowerAnalyzer
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an experiment design expert.
Suggest guardrail metrics to monitor during A/B tests.

Guardrails prevent unintended harm. Examples:
- If testing a payment flow, monitor revenue_per_user (direction: "decrease", threshold: -0.05)
- If testing a UI flow, monitor task_completion_time (direction: "increase", threshold: 0.10)
- If testing a backend/AI algorithm, monitor error_rate (direction: "increase", threshold: 0.01)

Return ONLY a valid JSON array of 2-3 guardrail objects with keys:
- name (snake_case string)
- threshold (float relative change, negative for decrease, positive for increase)
- direction ("decrease" or "increase")
- description (concise rationale)
"""


def _extract_json_array(raw_text: str) -> list[dict[str, Any]] | None:
    """Safely parse a JSON array from raw or markdown-fenced LLM output."""
    text = raw_text.strip()
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [item for item in parsed if isinstance(item, dict)]
            except json.JSONDecodeError:
                return None
    return None


class ExperimentDesignAgent(BaseAgent):
    """
    Generates complete ``ExperimentDesign`` blueprints from outcome hypotheses:
    1. Builds statistical ``ExperimentHypothesis`` (null, alternative, MDE, alpha, power)
    2. Computes per-variant sample size via ``PowerAnalyzer`` (``scipy.stats``)
    3. Suggests domain guardrail metrics via LLM (with deterministic schema fallback)
    4. Estimates experiment duration and stopping criteria
    """

    def __init__(
        self,
        *,
        confidence_level: float = 0.95,
        statistical_power: float = 0.80,
        default_daily_traffic: int = 1000,
        default_test_type: StatisticalTestType = StatisticalTestType.TTEST,
    ) -> None:
        super().__init__(
            name="experiment-design-agent",
            version="0.2.0",
            capabilities=[
                "experiment_design",
                "power_analysis",
                "guardrail_recommendation",
            ],
            required_tools=[],
            input_schema="https://pvs.ai/schemas/outcome.schema.json",
            output_schema="https://pvs.ai/schemas/experiment_design.schema.json",
        )
        self.confidence_level = confidence_level
        self.statistical_power = statistical_power
        self.default_daily_traffic = default_daily_traffic
        self.default_test_type = default_test_type

    def _validate_input(self, input_artifact: dict[str, Any]) -> None:
        """Accept both official ``outcome.schema.json`` and simplified outcome dicts."""
        if not isinstance(input_artifact, dict):
            raise ValueError("Outcome input artifact must be a dictionary")
        has_sc = isinstance(input_artifact.get("success_criteria"), dict)
        has_pm = isinstance(input_artifact.get("primary_metric"), dict)
        if not (has_sc or has_pm):
            raise ValueError(
                "Outcome artifact must contain 'success_criteria' or 'primary_metric'"
            )

    def _extract_outcome_fields(self, outcome: dict[str, Any]) -> dict[str, Any]:
        """Normalize official ``outcome.schema.json`` or simplified outcome dict."""
        sc = outcome.get("success_criteria")
        if isinstance(sc, dict):
            pm = sc.get("primary_metric", {})
            metric_name = (
                str(pm.get("name", "primary_metric")) if isinstance(pm, dict) else str(pm)
            )
            metric_unit = str(pm.get("unit", "")) if isinstance(pm, dict) else ""
            baseline = float(sc["baseline"])
            target = float(sc["target"])
            existing_guardrails = sc.get("guardrails", [])
        else:
            pm = outcome["primary_metric"]
            metric_name = str(pm.get("name", "primary_metric"))
            metric_unit = str(pm.get("unit", ""))
            baseline = float(pm["baseline"])
            target = float(pm["target"])
            existing_guardrails = outcome.get("guardrails", [])

        hypothesis_text = str(
            outcome.get("hypothesis", f"Improve {metric_name} from {baseline} to {target}")
        )
        unit_lower = metric_unit.lower()
        name_lower = metric_name.lower()
        is_proportion = (
            0.0 < baseline < 1.0
            and 0.0 < target < 1.0
            and (
                "rate" in name_lower
                or "conversion" in name_lower
                or "ratio" in unit_lower
                or "proportion" in unit_lower
            )
        )
        return {
            "outcome_id": outcome.get("id"),
            "hypothesis_text": hypothesis_text,
            "metric_name": metric_name,
            "baseline": baseline,
            "target": target,
            "metric_type": str(
                outcome.get("metric_type", "proportion" if is_proportion else "continuous")
            ),
            "daily_traffic": int(outcome.get("daily_traffic", self.default_daily_traffic)),
            "existing_guardrails": (
                existing_guardrails if isinstance(existing_guardrails, list) else []
            ),
        }

    def _build_hypothesis(self, outcome: dict[str, Any]) -> ExperimentHypothesis:
        """Build statistical ``ExperimentHypothesis`` contract from an outcome artifact."""
        fields = self._extract_outcome_fields(outcome)
        baseline = fields["baseline"]
        target = fields["target"]
        metric_name = fields["metric_name"]

        denom = abs(baseline) if abs(baseline) > 1e-9 else 1.0
        mde = abs(target - baseline) / denom
        if mde <= 0.0:
            mde = 0.05

        direction_word = "increases" if target >= baseline else "decreases"
        return ExperimentHypothesis(
            null_hypothesis=f"No difference in {metric_name} between control and treatment",
            alternative_hypothesis=(
                f"{metric_name} {direction_word} from {baseline} to {target} "
                f"(relative MDE >= {mde:.1%})"
            ),
            primary_metric=metric_name,
            baseline_value=baseline,
            expected_value=target,
            minimum_detectable_effect=mde,
            confidence_level=self.confidence_level,
            statistical_power=self.statistical_power,
            metric_type=fields["metric_type"],
        )

    def _compute_sample_size(
        self,
        hypothesis: ExperimentHypothesis,
        *,
        std_dev: float | None = None,
    ) -> int:
        """Compute required per-variant sample size via ``PowerAnalyzer``."""
        return PowerAnalyzer.compute_sample_size(hypothesis, std_dev=std_dev)

    def _estimate_duration(self, sample_size: int, outcome: dict[str, Any]) -> int:
        """Estimate experiment duration in days clamped to [7, 30]."""
        daily_traffic = int(outcome.get("daily_traffic", self.default_daily_traffic))
        return PowerAnalyzer.estimate_duration_days(
            sample_size,
            daily_traffic=daily_traffic,
            treatment_allocation=0.5,
            min_days=7,
            max_days=30,
        )

    def _default_guardrails(self, fields: dict[str, Any]) -> list[GuardrailMetric]:
        """Build deterministic fallback guardrails from outcome schema or SRE defaults."""
        guardrails: list[GuardrailMetric] = []
        for raw in fields.get("existing_guardrails", []):
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("metric") or raw.get("name") or "").strip()
            if not name:
                continue
            raw_dir = str(raw.get("direction", "decrease")).lower()
            direction = "increase" if raw_dir in {"above", "increase"} else "decrease"
            raw_thresh = float(raw.get("threshold", 0.05))
            # Convert positive absolute/ratio thresholds into signed relative change defaults
            if direction == "decrease":
                threshold = -abs(raw_thresh) if abs(raw_thresh) <= 1.0 else -0.05
            else:
                threshold = abs(raw_thresh) if abs(raw_thresh) <= 1.0 else 0.05
            guardrails.append(
                GuardrailMetric(
                    name=name,
                    threshold=threshold,
                    direction=direction,
                    description=str(
                        raw.get(
                            "description",
                            f"Prevent regression on outcome guardrail '{name}'",
                        )
                    ),
                )
            )

        if not guardrails:
            guardrails = [
                GuardrailMetric(
                    name="error_rate",
                    threshold=0.01,
                    direction="increase",
                    description="Ensure experimental treatment does not increase error rate",
                ),
                GuardrailMetric(
                    name="latency_p95_ms",
                    threshold=0.10,
                    direction="increase",
                    description="Ensure experimental treatment does not degrade p95 latency >10%",
                ),
                GuardrailMetric(
                    name="retention_rate",
                    threshold=-0.05,
                    direction="decrease",
                    description="Ensure experimental treatment does not reduce retention >5%",
                ),
            ]
        return guardrails

    async def _suggest_guardrails(
        self,
        outcome: dict[str, Any],
        harness: Harness | None,
    ) -> list[GuardrailMetric]:
        """Suggest guardrail metrics via LLM with deterministic safe fallback."""
        fields = self._extract_outcome_fields(outcome)
        fallback = self._default_guardrails(fields)
        if harness is None:
            return fallback

        user_prompt = (
            f"Outcome hypothesis:\n{fields['hypothesis_text']}\n\n"
            f"Primary metric: {fields['metric_name']}\n"
            f"Baseline: {fields['baseline']} -> Target: {fields['target']}\n\n"
            "Suggest 2-3 guardrail metrics as a JSON array."
        )
        try:
            response = await harness.invoke_model(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
            )
            parsed = _extract_json_array(response.content)
            if parsed:
                suggested: list[GuardrailMetric] = []
                for item in parsed:
                    if {"name", "threshold", "direction"}.issubset(item.keys()):
                        suggested.append(
                            GuardrailMetric(
                                name=str(item["name"]),
                                threshold=float(item["threshold"]),
                                direction=str(item["direction"]),
                                description=str(item.get("description", "")),
                            )
                        )
                if suggested:
                    return suggested
        except Exception as exc:
            logger.debug("LLM guardrail suggestion fell back to defaults: %s", exc)

        return fallback

    async def _execute_impl(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,  # noqa: ARG002
    ) -> AgentResult:
        """Generate a complete ``ExperimentDesign`` artifact from an outcome hypothesis."""
        fields = self._extract_outcome_fields(input_artifact)
        hypothesis = self._build_hypothesis(input_artifact)
        sample_size = self._compute_sample_size(hypothesis)
        duration_days = self._estimate_duration(sample_size, input_artifact)
        guardrails = await self._suggest_guardrails(input_artifact, harness)

        test_type = (
            StatisticalTestType.CHI_SQUARE
            if hypothesis.metric_type == "proportion"
            and self.default_test_type == StatisticalTestType.TTEST
            else self.default_test_type
        )

        design = ExperimentDesign(
            id=f"experiment-{uuid.uuid4().hex[:8]}",
            outcome_id=str(fields["outcome_id"]) if fields["outcome_id"] else None,
            test_type=test_type,
            hypothesis=hypothesis,
            variants={
                "control": {"description": "Current baseline experience"},
                "treatment": {"description": fields["hypothesis_text"]},
            },
            sample_size_per_variant=sample_size,
            duration_days=duration_days,
            guardrails=guardrails,
            stopping_criteria={
                "early_stop_enabled": True,
                "early_stop_alpha": 0.01,
                "max_duration_days": duration_days,
                "guardrail_violation_action": "abort",
            },
            created_at=datetime.now(UTC),
        )

        return AgentResult(
            success=True,
            output_artifact=design.to_dict(),
            iterations=1,
            cost_usd=float(harness.budget.current_cost_usd),
            duration_seconds=0.0,
            reasoning_trace=[
                f"Constructed hypothesis for primary metric '{hypothesis.primary_metric}' "
                f"(MDE={hypothesis.minimum_detectable_effect:.2%})",
                f"Computed sample_size_per_variant={sample_size} "
                f"(alpha={1.0 - hypothesis.confidence_level:.2f}, "
                f"power={hypothesis.statistical_power:.2f})",
                f"Estimated duration={duration_days} days with {len(guardrails)} guardrails",
            ],
            tool_calls=[],
            checkpoints=[],
        )

"""
Measurement Agent executing the Learn stage of the product value stream (EVO-010).

Queries production analytics via ``OutcomeTracker``, evaluates statistical
significance and guardrails, attributes development cost to outcome improvements,
and optionally synthesizes qualitative insights with an LLM.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from product_value_system.adapters.model_adapter import ModelAdapter
from product_value_system.agents.agent import AgentResult
from product_value_system.agents.base_agent import BaseAgent
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.learning.outcome_tracker import OutcomeMeasurement, OutcomeTracker


class MeasurementAgent(BaseAgent):
    """
    Agent that measures deployed product outcomes against baseline hypotheses
    and generates structured learnings for the Discovery feedback loop.
    """

    def __init__(
        self,
        outcome_tracker: OutcomeTracker,
        model_adapter: ModelAdapter | None = None,
        name: str = "measurement-agent",
        enable_llm_insights: bool | None = None,
    ) -> None:
        super().__init__(
            name=name,
            version="0.1.0",
            capabilities=[
                "outcome_measurement",
                "statistical_evaluation",
                "cost_attribution",
                "learning_synthesis",
            ],
            required_tools=["analytics.query"],
            input_schema="https://pvs.ai/schemas/outcome.schema.json",
            output_schema="https://pvs.ai/schemas/outcome.schema.json",
        )
        self.outcome_tracker = outcome_tracker
        self.model_adapter = model_adapter
        self.enable_llm_insights = (
            enable_llm_insights if enable_llm_insights is not None else (model_adapter is not None)
        )

    async def _execute_impl(
        self,
        input_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> AgentResult:
        """
        Execute outcome measurement for a deployed outcome hypothesis.

        Expected ``input_artifact`` keys:
        - ``outcome_id`` (or ``id``): Outcome identifier
        - ``measurement_window_days``: Post-deployment window in days (default: 30)
        - ``baseline_window_days``: Pre-deployment baseline window in days (default: 30)
        - ``outcome_hypothesis`` (optional): Auto-registered if ``outcome_id`` not yet tracked
        - ``deployment_date`` (optional): ISO string or ``datetime`` for auto-registration
        - ``development_cost_usd`` (optional): Cost attributed if auto-registering
        - ``generate_llm_insights`` (optional): Override LLM insight generation
        """
        outcome_id = str(input_artifact.get("outcome_id") or input_artifact.get("id") or "")
        if not outcome_id:
            raise ValueError("input_artifact must include 'outcome_id' or 'id'")

        window_days = int(input_artifact.get("measurement_window_days", 30))
        baseline_window_days = int(input_artifact.get("baseline_window_days", 30))

        # Auto-register hypothesis if supplied directly in input_artifact
        if outcome_id not in self.outcome_tracker.deployments:
            hypothesis = input_artifact.get("outcome_hypothesis") or (
                input_artifact
                if ("success_criteria" in input_artifact or "primary_metric" in input_artifact)
                else None
            )
            if hypothesis is None:
                raise ValueError(
                    f"Outcome '{outcome_id}' is not registered and no "
                    "'outcome_hypothesis' was provided"
                )
            raw_deploy_date = input_artifact.get("deployment_date")
            if isinstance(raw_deploy_date, datetime):
                deploy_date = raw_deploy_date
            elif isinstance(raw_deploy_date, str):
                deploy_date = datetime.fromisoformat(raw_deploy_date)
            else:
                deploy_date = datetime.now(UTC) - timedelta(days=window_days)

            dev_cost = input_artifact.get("development_cost_usd")
            self.outcome_tracker.register_deployment(
                outcome_id=outcome_id,
                deployment_date=deploy_date,
                outcome_hypothesis=hypothesis,
                development_cost_usd=float(dev_cost) if dev_cost is not None else None,
            )

        measurement = await self.outcome_tracker.measure_outcome(
            outcome_id=outcome_id,
            measurement_window_days=window_days,
            baseline_window_days=baseline_window_days,
        )

        reasoning_trace = [
            f"Measured outcome '{outcome_id}' over {window_days}-day window",
            (
                f"Metric '{measurement.primary_metric_name}': "
                f"baseline={measurement.baseline_value}, "
                f"current={measurement.current_value}, "
                f"target={measurement.target_value}, "
                f"status={measurement.status} (p={measurement.p_value:.4f})"
            ),
        ]

        output_artifact = measurement.to_dict()
        output_artifact["measured_at"] = datetime.now(UTC).isoformat()

        use_llm = bool(input_artifact.get("generate_llm_insights", self.enable_llm_insights))
        if use_llm:
            llm_insights = await self._generate_llm_insights(
                measurement=measurement,
                harness=harness,
                context=context,
            )
            output_artifact["llm_insights"] = llm_insights
            reasoning_trace.append("Generated qualitative LLM insight synthesis")

        return AgentResult(
            success=True,
            output_artifact=output_artifact,
            iterations=max(1, harness.budget.current_iterations),
            cost_usd=float(harness.budget.current_cost_usd),
            duration_seconds=0.0,
            reasoning_trace=reasoning_trace,
            tool_calls=[{"tool": "analytics.query", "outcome_id": outcome_id}],
            checkpoints=[],
        )

    async def _generate_llm_insights(
        self,
        measurement: OutcomeMeasurement,
        harness: Harness,
        context: ExecutionContext,
    ) -> str:
        """Synthesize executive learnings and next-iteration ideas using an LLM."""
        prompt = self._build_insight_prompt(measurement)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a product analytics and outcome-learning specialist. "
                    "Given quantitative post-launch metrics, synthesize concise root-cause "
                    "hypotheses, key takeaways, and concrete next discovery experiments."
                ),
            },
            {"role": "user", "content": prompt},
        ]

        active_harness = harness
        if self.model_adapter is not None and harness.model_adapter is not self.model_adapter:
            active_harness = Harness(
                context=context,
                budget=harness.budget,
                guardrails=harness.guardrails,
                model_adapter=self.model_adapter,
                input_validator=harness.input_validator,
            )

        response = await active_harness.invoke_model(
            messages=messages,
            temperature=0.3,
            max_tokens=600,
        )
        return str(response.content).strip()

    def _build_insight_prompt(self, measurement: OutcomeMeasurement) -> str:
        """Build structured prompt summarizing quantitative outcome measurement."""
        guardrail_lines = (
            "\n".join(
                f"- {g['metric']}: {g['value']} "
                f"(threshold {g['direction']} {g['threshold']}, passed={g['passed']})"
                for g in measurement.guardrail_results
            )
            if measurement.guardrail_results
            else "- None configured"
        )
        cost_attr = measurement.cost_attribution
        return (
            f"Analyze post-launch outcome measurement for `{measurement.outcome_id}`:\n\n"
            f"- Primary Metric: {measurement.primary_metric_name}\n"
            f"- Baseline: {measurement.baseline_value}\n"
            f"- Target: {measurement.target_value}\n"
            f"- Current: {measurement.current_value}\n"
            f"- Status: {measurement.status} (p={measurement.p_value:.4f})\n"
            f"- Confidence Interval: {measurement.confidence_interval}\n"
            f"- Development Cost (USD): ${cost_attr.get('development_cost_usd', 0.0):.2f}\n"
            f"- Cost per Unit Improvement: {cost_attr.get('cost_per_unit_improvement')}\n\n"
            f"Guardrails:\n{guardrail_lines}\n\n"
            f"Automated Learnings:\n"
            + "\n".join(f"- {item}" for item in measurement.learning)
            + "\n\nProvide 3 concise, actionable learnings to feed back into product discovery."
        )

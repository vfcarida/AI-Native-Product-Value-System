"""
Discovery agent: Problem -> Outcome hypothesis.
"""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced
from product_value_system.simulation.persona_factory import PersonaFactory
from product_value_system.simulation.scenario_runner import ScenarioRunner
from product_value_system.simulation.tinytroupe_adapter import (
    SimulationResult,
    TinyTroupeAdapter,
)

logger = logging.getLogger(__name__)
DEFAULT_TEMPLATES_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "simulation" / "persona_templates.yaml"
)


class DiscoveryAgent(BaseAgent):
    """
    Discovery agent analyzes problems and generates outcome hypotheses.

    Process:
    1. Load problem statement
    2. Analyze context and constraints
    3. Generate measurable outcome hypothesis
    4. Define success criteria (baseline, target, guardrails)
    5. Specify evidence requirements
    6. Validate output against schema
    """

    def __init__(
        self,
        schema_validator: SchemaValidator,
        knowledge_base_path: Path | None = None,
        *,
        enable_simulation: bool = False,
        simulation_budget_usd: float = 50.0,
        scenario_runner: ScenarioRunner | None = None,
        persona_templates_path: Path | str | None = None,
    ) -> None:
        """
        Initialize discovery agent.

        Args:
            schema_validator: Schema validator for problem/outcome
            knowledge_base_path: Path to knowledge base (optional)
            enable_simulation: Run optional TinyTroupe synthetic customer validation
            simulation_budget_usd: Max USD budget allocated to simulation
            scenario_runner: Optional pre-configured ScenarioRunner (useful for DI/testing)
            persona_templates_path: Path to persona_templates.yaml
        """
        super().__init__(
            name="discovery-agent",
            version="0.1.0",
            capabilities=["problem_analysis", "outcome_generation"],
            required_tools=["knowledge.query", "document.read", "schema.validate"],
            input_schema="https://pvs.ai/schemas/problem.schema.json",
            output_schema="https://pvs.ai/schemas/outcome.schema.json",
        )
        self.schema_validator = schema_validator
        self.knowledge_base_path = knowledge_base_path
        self.enable_simulation = enable_simulation
        self.simulation_budget_usd = simulation_budget_usd
        self._persona_templates_path = (
            Path(persona_templates_path)
            if persona_templates_path is not None
            else DEFAULT_TEMPLATES_PATH
        )
        self.scenario_runner = scenario_runner

        if self.enable_simulation and self.scenario_runner is None:
            sim_adapter = TinyTroupeAdapter(max_cost_usd=self.simulation_budget_usd)
            factory = PersonaFactory(self._persona_templates_path)
            self.scenario_runner = ScenarioRunner(sim_adapter, factory)

    @traced("discovery_agent.execute")
    async def execute(  # type: ignore[override]
        self,
        problem_artifact: dict[str, Any],
        harness: Harness,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        """
        Execute discovery process.

        Args:
            problem_artifact: Validated problem.yaml content
            harness: Harness providing model, tools, memory
            context: Execution context (run_id, budgets, etc.)

        Returns:
            Outcome hypothesis artifact (dict conforming to outcome.schema.json)
        """
        self.schema_validator.validate(problem_artifact, "problem.schema.json")

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_discovery_prompt(problem_artifact)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await harness.invoke_model(
            messages=messages,
            temperature=0.4,
            max_tokens=2000,
        )

        outcome = self._parse_outcome_response(
            response.content,
            problem_artifact,
        )

        # Optional synthetic customer simulation validation (EVO-009)
        run_sim = self.enable_simulation or bool(
            getattr(context, "metadata", {}).get("enable_simulation", False)
        )
        if run_sim:
            try:
                simulation_report = await self._simulate_outcome(
                    outcome=outcome,
                    problem=problem_artifact,
                )
                outcome["simulation_evidence"] = {
                    "status": "completed",
                    "scenario_name": simulation_report.scenario_name,
                    "population_size": simulation_report.population_size,
                    "aggregated_insights": simulation_report.aggregated_insights,
                    "quality_metrics": simulation_report.quality_metrics,
                    "cost_usd": simulation_report.cost_usd,
                    "confidence": self._compute_confidence(simulation_report),
                    "disclaimer": simulation_report.disclaimer,
                }
                harness.budget.spend(Decimal(str(simulation_report.cost_usd)))
            except Exception as exc:
                logger.warning("Customer simulation failed (discovery continues): %s", exc)
                outcome["simulation_evidence"] = {
                    "status": "failed",
                    "error": str(exc),
                }

        self.schema_validator.validate(outcome, "outcome.schema.json")

        return outcome

    async def _simulate_outcome(
        self,
        outcome: dict[str, Any],
        problem: dict[str, Any],
    ) -> SimulationResult:
        """Execute synthetic customer simulation to explore outcome hypothesis."""
        if self.scenario_runner is None:
            sim_adapter = TinyTroupeAdapter(max_cost_usd=self.simulation_budget_usd)
            factory = PersonaFactory(self._persona_templates_path)
            self.scenario_runner = ScenarioRunner(sim_adapter, factory)

        return await self.scenario_runner.run_hypothesis_validation(
            outcome_hypothesis=outcome,
            problem_context=problem.get("context", {}),
        )

    def _compute_confidence(self, simulation_report: SimulationResult) -> float:
        """Compute weighted confidence score [0.0, 1.0] from simulation quality metrics."""
        quality = simulation_report.quality_metrics
        weights = {
            "persona_adherence": 0.4,
            "interaction_realism": 0.3,
            "response_diversity": 0.3,
        }
        score = sum(float(quality.get(metric, 0.0)) * weight for metric, weight in weights.items())
        return round(max(0.0, min(1.0, score)), 3)

    def _build_system_prompt(self) -> str:
        """Build system prompt for discovery agent."""
        return """You are a product discovery agent specializing in outcome-driven analysis.

Your role:
- Analyze problem statements and user contexts
- Generate measurable outcome hypotheses
- Define success criteria with baseline and target metrics
- Specify evidence requirements

Principles:
- Focus on USER OUTCOMES, not features
- Make hypotheses MEASURABLE (quantify success)
- Define GUARDRAILS (metrics that must not regress)
- Require EVIDENCE before proceeding to build
- Be CONSERVATIVE: recommend more discovery if uncertain

Output format:
Provide a structured JSON response with:
- hypothesis: Clear statement of expected outcome
- success_criteria: Primary metric with baseline/target, plus guardrails
- evidence_requirements: What evidence is needed to validate hypothesis

Use data from problem context and business research. Do not hallucinate numbers."""

    def _build_discovery_prompt(self, problem: dict[str, Any]) -> str:
        """Build user prompt with problem details."""
        return f"""Analyze this problem and generate an outcome hypothesis:

## Problem Statement
{problem['statement']}

## Context
User Segment: {problem['context']['user_segment']}
Business Context: {problem['context']['business_context']}

## Constraints
{json.dumps(problem['constraints'], indent=2)}

## Task
Generate an outcome hypothesis with:
1. Clear, measurable expected outcome
2. Primary success metric (name, unit, baseline, target)
3. Guardrail metrics (metrics that must not regress)
4. Evidence requirements (what we need to validate this hypothesis)

Respond in JSON format matching this structure:
{{
  "hypothesis": "Clear statement of expected outcome",
  "success_criteria": {{
    "primary_metric": {{
      "name": "metric name",
      "unit": "unit of measurement",
      "measurement_method": "how to measure"
    }},
    "baseline": 0.0,
    "target": 0.0,
    "guardrails": [
      {{"metric": "guardrail name", "threshold": 0.0, "direction": "above"}}
    ]
  }},
  "evidence_requirements": [
    {{"type": "qualitative", "description": "...", "mandatory": true}}
  ]
}}"""

    def _parse_outcome_response(
        self,
        response_content: str,
        problem: dict[str, Any],
    ) -> dict[str, Any]:
        """Parse model response into outcome artifact."""
        content = response_content.strip()
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].split("```")[0].strip()

        parsed: dict[str, Any] = json.loads(content)

        problem_id = str(problem["id"])
        outcome_id = problem_id.replace("problem-", "outcome-", 1)

        now = datetime.now(UTC).isoformat()

        outcome: dict[str, Any] = {
            "id": outcome_id,
            "version": 1,
            "created": now,
            "updated": now,
            "problem_id": problem_id,
            "hypothesis": parsed["hypothesis"],
            "success_criteria": parsed["success_criteria"],
            "evidence_requirements": parsed.get("evidence_requirements", []),
        }

        return outcome

"""
Discovery agent: Problem -> Outcome hypothesis.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.agents.base_agent import BaseAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import traced


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
    ) -> None:
        """
        Initialize discovery agent.

        Args:
            schema_validator: Schema validator for problem/outcome
            knowledge_base_path: Path to knowledge base (optional)
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

        self.schema_validator.validate(outcome, "outcome.schema.json")

        return outcome

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

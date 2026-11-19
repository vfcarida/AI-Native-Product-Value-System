"""
End-to-end real integration tests for DiscoveryAgent with live LLM API (EVO-003).
"""

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from product_value_system.adapters.openai_adapter import OpenAIAdapter
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.budget import Budget, BudgetExceededError
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails
from product_value_system.harness.harness import Harness
from tests.integration_real.conftest import (
    RealIntegrationCostTracker,
    real_tests_enabled,
)


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled() or not os.getenv("OPENAI_API_KEY"),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1 and OPENAI_API_KEY",
)
class TestDiscoveryAgentE2E:
    """End-to-end tests for DiscoveryAgent with real LLM."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_discovery_agent_generates_outcome(
        self,
        schemas_dir: Path,
        cost_tracker: RealIntegrationCostTracker,
    ) -> None:
        """Test DiscoveryAgent generates schema-valid outcome from live LLM."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            validator = SchemaValidator(schemas_dir)
            agent = DiscoveryAgent(schema_validator=validator)
            budget = Budget(max_cost_usd=Decimal("0.05"), max_iterations=5)
            context = ExecutionContext(run_id="real-e2e-discovery-001")
            harness = Harness(
                context=context,
                model_adapter=adapter,
                budget=budget,
                guardrails=Guardrails(max_iterations=5),
            )

            problem = {
                "id": "problem-payment-notifications-real",
                "version": 1,
                "created": datetime.now(UTC).isoformat(),
                "statement": "Users lack timely payment completion notifications",
                "context": {
                    "user_segment": "Mobile banking users",
                    "business_context": "Manual app refresh required; target 95% delivery rate",
                },
                "constraints": {
                    "no_pii": True,
                    "synthetic_only": True,
                    "max_cost_usd": 0.05,
                },
            }

            outcome = await agent.execute(problem, harness, context)
            cost_tracker.track(float(harness.budget.current_cost_usd))

            assert outcome is not None
            assert outcome["problem_id"] == problem["id"]
            assert "hypothesis" in outcome
            assert "success_criteria" in outcome
            assert "primary_metric" in outcome["success_criteria"]
            assert harness.budget.current_cost_usd > Decimal("0.0")
            assert harness.budget.current_cost_usd <= Decimal("0.05")
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_discovery_agent_respects_budget(
        self,
        schemas_dir: Path,
        cost_tracker: RealIntegrationCostTracker,
    ) -> None:
        """Test DiscoveryAgent enforces tiny USD budget via Harness."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            validator = SchemaValidator(schemas_dir)
            agent = DiscoveryAgent(schema_validator=validator)
            budget = Budget(max_cost_usd=Decimal("0.000001"), max_iterations=5)
            context = ExecutionContext(run_id="real-e2e-discovery-budget")
            harness = Harness(
                context=context,
                model_adapter=adapter,
                budget=budget,
                guardrails=Guardrails(max_iterations=5),
            )

            problem = {
                "id": "problem-budget-cap-real",
                "version": 1,
                "created": datetime.now(UTC).isoformat(),
                "statement": "Reduce latency for instant account alerts",
                "context": {
                    "user_segment": "Retail banking customers",
                    "business_context": "Tight budget test",
                },
                "constraints": {
                    "no_pii": True,
                    "synthetic_only": True,
                    "max_cost_usd": 0.000001,
                },
            }

            with pytest.raises(BudgetExceededError):
                await agent.execute(problem, harness, context)

            cost_tracker.track(float(harness.budget.current_cost_usd))
        finally:
            await adapter.close()

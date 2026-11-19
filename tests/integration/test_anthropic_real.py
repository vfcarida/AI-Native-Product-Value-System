"""
Real integration tests for AnthropicAdapter with DiscoveryAgent (EVO-001).
Skipped automatically in CI when ANTHROPIC_API_KEY is not configured.
"""

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from product_value_system.adapters.anthropic_adapter import AnthropicAdapter
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.budget import Budget
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="Requires ANTHROPIC_API_KEY environment variable",
)
@pytest.mark.integration
class TestAnthropicIntegration:
    """Real integration tests (skipped in CI without API key)."""

    @pytest.mark.asyncio
    async def test_discovery_agent_with_anthropic(self) -> None:
        """Test DiscoveryAgent works unmodified with AnthropicAdapter."""
        schema_dir = Path(__file__).parent.parent.parent / "schemas"
        validator = SchemaValidator(schema_dir)

        adapter = AnthropicAdapter(model="claude-3-5-sonnet-20241022")
        agent = DiscoveryAgent(schema_validator=validator)
        harness = Harness(
            model_adapter=adapter,
            budget=Budget(max_cost_usd=Decimal("1.0")),
        )
        context = ExecutionContext(run_id="anthropic-integration-001")

        problem = {
            "id": "problem-fast-notifications-001",
            "version": 1,
            "created": datetime.now(UTC).isoformat(),
            "statement": "Users want faster payment notifications in the mobile app",
            "context": {
                "user_segment": "Mobile banking users",
                "business_context": "Improve user trust and reduce support calls",
            },
            "constraints": {
                "no_pii": True,
                "synthetic_only": True,
                "max_cost_usd": 1.0,
            },
        }

        outcome = await agent.execute(problem, harness, context)

        assert outcome is not None
        assert "hypothesis" in outcome
        assert outcome["problem_id"] == problem["id"]
        assert harness.budget.current_cost_usd < Decimal("1.0")

        await adapter.close()

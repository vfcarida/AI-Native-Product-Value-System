"""
End-to-end real integration tests for BuildAgent with live Docker + LLM (EVO-003).
"""

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import docker
import pytest

from product_value_system.adapters.openai_adapter import OpenAIAdapter
from product_value_system.agents.build_agent import BuildAgent
from product_value_system.agents.discovery_agent import DiscoveryAgent
from product_value_system.agents.specification_agent import SpecificationAgent
from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.budget import Budget
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails
from product_value_system.harness.harness import Harness
from product_value_system.runtime.docker_sandbox import DockerSandboxProvider
from tests.integration_real.conftest import (
    RealIntegrationCostTracker,
    docker_daemon_available,
    real_tests_enabled,
)


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled()
    or not os.getenv("OPENAI_API_KEY")
    or not docker_daemon_available(),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1, OPENAI_API_KEY, and active Docker daemon",
)
class TestBuildAgentE2E:
    """End-to-end tests combining live OpenAI LLM and real Docker sandbox."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)
    async def test_build_agent_executes_in_real_docker(
        self,
        schemas_dir: Path,
        cost_tracker: RealIntegrationCostTracker,
    ) -> None:
        """Test BuildAgent generates code & tests and executes them in real Docker."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        sandbox_provider = DockerSandboxProvider()
        try:
            validator = SchemaValidator(schemas_dir)
            agent = BuildAgent(
                schema_validator=validator,
                sandbox_provider=sandbox_provider,
            )
            context = ExecutionContext(run_id="real-e2e-build-001")
            harness = Harness(
                context=context,
                model_adapter=adapter,
                budget=Budget(max_cost_usd=Decimal("0.05"), max_iterations=10),
                guardrails=Guardrails(max_iterations=10),
            )

            now = datetime.now(UTC).isoformat()
            spec = {
                "id": "spec-real-calc-001",
                "version": 1,
                "created": now,
                "updated": now,
                "outcome_id": "outcome-real-calc-001",
                "title": "Simple Fee Calculator",
                "overview": "Compute flat percentage transaction fee.",
                "functional_requirements": [
                    {
                        "id": "FR-001",
                        "description": "Calculate 2.5% transaction fee rounded to 2 decimals",
                        "priority": "must",
                        "acceptance_criteria": [
                            "calculate_fee(100.0) returns 2.5",
                            "Negative amounts raise ValueError",
                        ],
                    }
                ],
                "quality_attributes": {"test_coverage_min": 80},
                "constraints": {"technical": ["Python 3.12 standard library only"]},
            }

            result = await agent.execute(spec, harness, context)
            cost_tracker.track(float(harness.budget.current_cost_usd))

            assert result["specification_id"] == spec["id"]
            assert result["requirements_total"] == 1
            assert len(result["build_results"]) == 1

            # Verify no leaked pvs-sandbox containers remain after destroy()
            client = docker.from_env()
            remaining = [
                c.name
                for c in client.containers.list(all=True)
                if "pvs-sandbox" in (c.name or "")
            ]
            assert remaining == []
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(240)
    async def test_full_workflow_discovery_spec_build(
        self,
        schemas_dir: Path,
        cost_tracker: RealIntegrationCostTracker,
    ) -> None:
        """Test full real pipeline: DiscoveryAgent -> SpecificationAgent -> BuildAgent."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        sandbox_provider = DockerSandboxProvider()
        try:
            validator = SchemaValidator(schemas_dir)
            context = ExecutionContext(run_id="real-e2e-full-001")
            harness = Harness(
                context=context,
                model_adapter=adapter,
                budget=Budget(max_cost_usd=Decimal("0.05"), max_iterations=15),
                guardrails=Guardrails(max_iterations=15),
            )

            problem = {
                "id": "problem-fee-calc-e2e",
                "version": 1,
                "created": datetime.now(UTC).isoformat(),
                "statement": "Merchants need a deterministic fee calculation utility",
                "context": {
                    "user_segment": "Small merchants",
                    "business_context": "Improve billing transparency",
                },
                "constraints": {
                    "no_pii": True,
                    "synthetic_only": True,
                    "max_cost_usd": 0.05,
                },
            }

            discovery_agent = DiscoveryAgent(schema_validator=validator)
            outcome = await discovery_agent.execute(problem, harness, context)
            assert "hypothesis" in outcome

            spec_agent = SpecificationAgent(schema_validator=validator)
            spec = await spec_agent.execute(outcome, harness, context)
            assert "functional_requirements" in spec

            # Keep real build bounded to 1 requirement for cost control
            spec["functional_requirements"] = spec["functional_requirements"][:1]
            build_agent = BuildAgent(
                schema_validator=validator,
                sandbox_provider=sandbox_provider,
            )
            build_result = await build_agent.execute(spec, harness, context)
            cost_tracker.track(float(harness.budget.current_cost_usd))

            assert build_result["specification_id"] == spec["id"]
            assert build_result["requirements_total"] == 1
        finally:
            await adapter.close()

"""
Real integration tests against Cedar CLI and PolicyEngine (EVO-003).
"""

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from product_value_system.policy.engine import Decision, PolicyEngine
from product_value_system.policy.entities import (
    Agent,
    AgentRole,
    AuthorizationContext,
    Classification,
    RiskLevel,
    Tool,
    TrustLevel,
)
from tests.integration_real.conftest import (
    cedar_cli_available,
    real_tests_enabled,
)


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled(),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1",
)
class TestCedarPolicyReal:
    """Real integration tests with Cedar policies and Cedar CLI."""

    @pytest.fixture
    def policy_engine(self, policies_dir: Path) -> PolicyEngine:
        """Create PolicyEngine backed by repository Cedar policies."""
        return PolicyEngine(policies_dir=policies_dir)

    @pytest.mark.skipif(
        not cedar_cli_available(),
        reason="Cedar CLI binary not installed on PATH",
    )
    @pytest.mark.timeout(30)
    def test_cedar_cli_available(self) -> None:
        """Test Cedar CLI is installed and executable."""
        result = subprocess.run(
            ["cedar", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "cedar" in result.stdout.lower()

    @pytest.mark.timeout(30)
    def test_discovery_agent_knowledge_access_allowed(
        self, policy_engine: PolicyEngine
    ) -> None:
        """Test discovery agent is permitted to query knowledge_base in sandbox."""
        agent = Agent(
            id="agent-123",
            role=AgentRole(name="discovery", trust_level=TrustLevel.MEDIUM),
            version="0.1.0",
            cost_budget=Decimal("10.0"),
            allowed_environments={"sandbox"},
        )
        tool = Tool(
            name="knowledge_base",
            classification=Classification.READ,
            risk_level=RiskLevel.LOW,
            data_access={"internal"},
            requires_approval=False,
        )
        context = AuthorizationContext(
            environment="sandbox",
            has_human_approval=False,
            cost_so_far=Decimal("0.0"),
            iteration_count=0,
            stage="discovery",
        )

        decision = policy_engine.authorize(
            principal=agent,
            action="query",
            resource=tool,
            context=context,
        )
        assert decision.decision == Decision.ALLOW
        assert decision.is_allowed() is True

    @pytest.mark.timeout(30)
    def test_production_deploy_without_approval_denied(
        self, policy_engine: PolicyEngine
    ) -> None:
        """Test production deploy without human approval is denied."""
        agent = Agent(
            id="agent-456",
            role=AgentRole(name="build", trust_level=TrustLevel.MEDIUM),
            version="0.1.0",
            cost_budget=Decimal("10.0"),
            allowed_environments={"sandbox"},
        )
        context = AuthorizationContext(
            environment="production",
            has_human_approval=False,
            cost_so_far=Decimal("1.0"),
            iteration_count=1,
            stage="release",
        )

        decision = policy_engine.authorize(
            principal=agent,
            action="deploy",
            resource="production_cluster",
            context=context,
        )
        assert decision.decision == Decision.DENY
        assert "approval" in decision.reason.lower()

    @pytest.mark.timeout(30)
    def test_policy_fallback_consistency(self, policy_engine: PolicyEngine) -> None:
        """Test live PolicyEngine.authorize and deterministic fallback agree."""
        cases = [
            (
                {"role": "discovery", "name": "discovery-agent"},
                "query",
                {"type": "Tool", "name": "knowledge_base"},
                {"environment": "sandbox", "stage": "discovery"},
                Decision.ALLOW,
            ),
            (
                {"role": "build", "name": "build-agent"},
                "deploy",
                {"type": "Resource", "name": "production"},
                {"environment": "production", "has_human_approval": False},
                Decision.DENY,
            ),
            (
                {"role": "specification", "name": "specification-agent"},
                "write",
                {"type": "Tool", "name": "spec_editor"},
                {"environment": "sandbox", "stage": "specification"},
                Decision.ALLOW,
            ),
        ]

        for principal, action, resource, ctx, expected in cases:
            live_decision = policy_engine.authorize(principal, action, resource, ctx)
            request = policy_engine._build_request(principal, action, resource, ctx)
            fallback_decision = policy_engine._fallback_evaluate(request)

            assert live_decision.decision == fallback_decision.decision
            assert live_decision.decision == expected

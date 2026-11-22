"""
Policy engine unit tests for P004.
"""

import logging
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


@pytest.fixture
def policies_dir() -> Path:
    """Path to policies directory."""
    return Path(__file__).parent.parent.parent / "policies"


@pytest.fixture
def policy_engine(policies_dir: Path) -> PolicyEngine:
    """Policy engine instance."""
    return PolicyEngine(policies_dir)


@pytest.fixture
def discovery_agent() -> Agent:
    """Discovery agent entity."""
    role = AgentRole(name="discovery", trust_level=TrustLevel.MEDIUM)
    return Agent(
        id="discovery-001",
        role=role,
        version="0.1.0",
        cost_budget=Decimal("10.0"),
        allowed_environments={"sandbox"},
    )


@pytest.fixture
def knowledge_base_tool() -> Tool:
    """Knowledge base tool entity."""
    return Tool(
        name="knowledge_base",
        classification=Classification.READ,
        risk_level=RiskLevel.LOW,
        data_access={"filesystem"},
        requires_approval=False,
    )


@pytest.fixture
def sandbox_context() -> AuthorizationContext:
    """Sandbox execution context."""
    return AuthorizationContext(
        environment="sandbox",
        has_human_approval=False,
        cost_so_far=Decimal("1.5"),
        iteration_count=3,
        stage="discovery",
    )


@pytest.mark.unit
def test_policy_engine_loads(policy_engine: PolicyEngine) -> None:
    """Test that policy engine loads policies successfully."""
    assert policy_engine is not None
    assert policy_engine._policies_text is not None
    assert "permit" in policy_engine._policies_text
    assert "forbid" in policy_engine._policies_text


@pytest.mark.unit
def test_authorize_allow_discovery_query(
    policy_engine: PolicyEngine,
    discovery_agent: Agent,
    knowledge_base_tool: Tool,
    sandbox_context: AuthorizationContext,
) -> None:
    """Test that discovery agent can query knowledge base in sandbox."""
    decision = policy_engine.authorize(
        principal=discovery_agent,
        action="query",
        resource=knowledge_base_tool,
        context=sandbox_context,
    )

    assert decision.decision == Decision.ALLOW
    assert "sandbox" in decision.reason.lower()


@pytest.mark.unit
def test_authorize_deny_production_deploy_without_approval(
    policy_engine: PolicyEngine,
    discovery_agent: Agent,
) -> None:
    """Test that production deployment is denied without human approval."""
    context = AuthorizationContext(
        environment="production",
        has_human_approval=False,
        cost_so_far=Decimal("2.0"),
        iteration_count=1,
        stage="deploy",
    )

    decision = policy_engine.authorize(
        principal=discovery_agent,
        action="deploy",
        resource="production_environment",
        context=context,
    )

    assert decision.decision == Decision.DENY
    assert "approval" in decision.reason.lower()


@pytest.mark.unit
def test_authorize_deny_cost_budget_exceeded(
    policy_engine: PolicyEngine,
    discovery_agent: Agent,
    knowledge_base_tool: Tool,
) -> None:
    """Test that actions are denied when cost budget exceeded."""
    context = AuthorizationContext(
        environment="sandbox",
        has_human_approval=False,
        cost_so_far=Decimal("15.0"),  # Exceeds budget of 10.0
        iteration_count=1,
        stage="discovery",
    )

    decision = policy_engine.authorize(
        principal=discovery_agent,
        action="query",
        resource=knowledge_base_tool,
        context=context,
    )

    assert decision.decision == Decision.DENY
    assert "budget" in decision.reason.lower()


@pytest.mark.unit
def test_authorize_review_agent_read_only(policy_engine: PolicyEngine) -> None:
    """Test that review agents are allowed read and denied write."""
    review_role = AgentRole(name="security_review", trust_level=TrustLevel.HIGH)
    review_agent = Agent(
        id="sec-review-001",
        role=review_role,
        version="0.1.0",
        cost_budget=Decimal("5.0"),
        allowed_environments={"sandbox"},
    )
    context = AuthorizationContext(
        environment="sandbox",
        has_human_approval=False,
        cost_so_far=Decimal("0.5"),
        iteration_count=1,
        stage="review",
    )

    read_decision = policy_engine.authorize(
        principal=review_agent,
        action="read",
        resource="spec.md",
        context=context,
    )
    assert read_decision.decision == Decision.ALLOW

    write_decision = policy_engine.authorize(
        principal=review_agent,
        action="write",
        resource="spec.md",
        context=context,
    )
    assert write_decision.decision == Decision.DENY


@pytest.mark.unit
def test_authorize_logs_decision(
    policy_engine: PolicyEngine,
    discovery_agent: Agent,
    knowledge_base_tool: Tool,
    sandbox_context: AuthorizationContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that authorization decisions are logged."""
    with caplog.at_level(logging.INFO):
        policy_engine.authorize(
            principal=discovery_agent,
            action="query",
            resource=knowledge_base_tool,
            context=sandbox_context,
        )

    assert "Authorization" in caplog.text
    assert discovery_agent.id in caplog.text


@pytest.mark.unit
def test_cedar_entities_serialize() -> None:
    """Test that entities serialize to Cedar format correctly."""
    role = AgentRole(name="test", trust_level=TrustLevel.HIGH)
    entity = role.to_cedar_entity()

    assert entity["type"] == "ProductValueSystem::AgentRole"
    assert entity["id"] == "test"
    assert entity["attrs"]["trustLevel"] == "high"


@pytest.mark.unit
def test_policy_exact_cost_budget_and_prod_resource_boundaries(
    policy_engine: PolicyEngine,
    discovery_agent: Agent,
    knowledge_base_tool: Tool,
) -> None:
    """Pin exact cost_so_far == cost_budget denial and production-resource forbid rule."""
    # Exact budget boundary must trigger policy_forbid_cost_budget
    exact_budget_ctx = AuthorizationContext(
        environment="sandbox",
        has_human_approval=False,
        cost_so_far=discovery_agent.cost_budget,
        iteration_count=1,
        stage="discovery",
    )
    budget_decision = policy_engine.authorize(
        principal=discovery_agent,
        action="query",
        resource=knowledge_base_tool,
        context=exact_budget_ctx,
    )
    assert budget_decision.decision == Decision.DENY
    assert "policy_forbid_cost_budget" in budget_decision.policies_matched

    # Deploying to a production-named resource without approval must forbid even in staging env
    build_agent = Agent(
        id="build-boundary",
        role=AgentRole(name="build", trust_level=TrustLevel.HIGH),
        version="0.1.0",
        cost_budget=Decimal("50.0"),
        allowed_environments={"staging", "production"},
    )
    staging_ctx = AuthorizationContext(
        environment="staging",
        has_human_approval=False,
        cost_so_far=Decimal("1.0"),
        iteration_count=1,
        stage="build",
    )
    prod_res_decision = policy_engine.authorize(
        principal=build_agent,
        action="deploy",
        resource="production_cluster",
        context=staging_ctx,
    )
    assert prod_res_decision.decision == Decision.DENY
    assert "policy_forbid_prod_deploy" in prod_res_decision.policies_matched

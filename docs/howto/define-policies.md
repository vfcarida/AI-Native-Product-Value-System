# How-To: Define Cedar Policies

**Goal**: Create Cedar policies to control agent access to tools and resources.

**Time**: 20-30 minutes

**Prerequisites**:
- Understanding of [Cedar Policy Language](https://www.cedarpolicy.com/)
- Familiarity with [Policy Engine API](../reference/api/policy.md)

---

## Step 1: Understand Policy Requirement

Define what you want to control. Example:

> **Requirement**: Build agents can execute inside the sandbox environment (under 10 iterations) but NOT deploy to production without human approval.

---

## Step 2: Define Policy

Add or edit policies in `policies/agent_authorization.cedar`:

```cedar
// Allow build agent to execute in sandbox (max 10 iterations)
permit(
  principal,
  action == ProductValueSystem::Action::"execute",
  resource == ProductValueSystem::Tool::"sandbox"
) when {
  principal.role == ProductValueSystem::AgentRole::"build" &&
  context.environment == "sandbox" &&
  context.iteration_count < 10
};

// Forbid any agent from deploying to production without human approval
forbid(
  principal,
  action == ProductValueSystem::Action::"deploy",
  resource
) when {
  context.environment == "production" &&
  !context.has_human_approval
};
```

---

## Step 3: Define Policy Schema

Verify entity definitions in `policies/schema.cedarschema`:

```cedar
namespace ProductValueSystem {
  entity AgentRole;

  entity Agent in [AgentRole] = {
    "name": String,
    "role": AgentRole,
    "trustLevel": String,
    "costBudget": Long,
  };

  entity Tool = {
    "name": String,
    "classification": String,
    "riskLevel": String,
  };

  entity Resource = {
    "type": String,
    "classification": String,
  };

  action read, write, execute, deploy, query, call appliesTo {
    principal: [Agent, AgentRole],
    resource: [Tool, Resource],
    context: {
      "environment": String,
      "has_human_approval": Bool,
      "cost_so_far": Long,
      "iteration_count": Long,
      "stage": String,
    }
  };
}
```

---

## Step 4: Validate Policy

Run policy validation:

```bash
make validate-policies
```

Or validate directly with Python:

```bash
python -c "from product_value_system.policy.engine import PolicyEngine; PolicyEngine(); print('✓ All policies valid')"
```

Expected output:

```
✓ All policies valid
```

---

## Step 5: Write Policy Tests

Create: `tests/unit/test_build_agent_policies.py`

```python
"""
Tests for build agent policies.
"""

from decimal import Decimal
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
def policy_engine() -> PolicyEngine:
    """Policy engine with policies loaded."""
    return PolicyEngine(policies_dir="policies")


def test_build_agent_can_execute_in_sandbox(policy_engine: PolicyEngine) -> None:
    """Test build agent allowed to execute in sandbox."""
    decision = policy_engine.authorize(
        principal=Agent(
            id="build-1",
            name="build-agent",
            role=AgentRole(name="build"),
            trust_level=TrustLevel.MEDIUM,
            cost_budget=Decimal("5.0"),
        ),
        action="execute",
        resource=Tool(
            name="sandbox",
            classification=Classification.INTERNAL,
            risk_level=RiskLevel.MEDIUM,
        ),
        context=AuthorizationContext(
            environment="sandbox",
            has_human_approval=False,
            cost_so_far=Decimal("0.5"),
            iteration_count=2,
            stage="build",
        ),
    )

    assert decision.decision == Decision.ALLOW
    assert decision.is_allowed() is True


def test_build_agent_denied_production_deploy(policy_engine: PolicyEngine) -> None:
    """Test build agent denied production deployment without approval."""
    decision = policy_engine.authorize(
        principal=Agent(
            id="build-1",
            name="build-agent",
            role=AgentRole(name="build"),
            trust_level=TrustLevel.MEDIUM,
            cost_budget=Decimal("5.0"),
        ),
        action="deploy",
        resource="production_service",
        context=AuthorizationContext(
            environment="production",
            has_human_approval=False,
            cost_so_far=Decimal("0.5"),
            iteration_count=2,
            stage="build",
        ),
    )

    assert decision.decision == Decision.DENY
    assert decision.is_allowed() is False
```

Run:

```bash
pytest tests/unit/test_build_agent_policies.py -v
```

---

## Step 6: Add to CI

Ensure policies are validated in CI (`.github/workflows/ci.yml`):

```yaml
- name: Validate Cedar policies
  run: make validate-policies
```

---

## Common Policy Patterns

### Allow with Conditions

```cedar
permit(
  principal,
  action == ProductValueSystem::Action::"call",
  resource == ProductValueSystem::Tool::"external_api"
) when {
  context.has_human_approval == true &&
  principal.trustLevel == "high"
};
```

### Forbid Cost Overruns

```cedar
forbid(
  principal,
  action,
  resource
) when {
  context.cost_so_far >= principal.costBudget
};
```

### Role-Based Access

```cedar
permit(
  principal,
  action == ProductValueSystem::Action::"query",
  resource == ProductValueSystem::Tool::"knowledge_base"
) when {
  principal.role == ProductValueSystem::AgentRole::"discovery" &&
  context.environment == "sandbox"
};
```

---

## Troubleshooting

### Policy syntax error

**Error**: `Parse error at line 5: expected ';'`

**Solution**: Check Cedar syntax and ensure every `permit(...)` or `forbid(...)` statement ends with `;`.

---

### Policy not enforced

**Problem**: Agent is still allowed despite a rule check.

**Solution**: Verify `forbid` rules vs `permit` rules. In Cedar semantics, `forbid` always overrides `permit`, and default is `DENY`.

---

### Schema validation fails

**Error**: `Schema validation failed: unknown entity type`

**Solution**: Ensure all entity types and context attributes used in `agent_authorization.cedar` are declared in `policies/schema.cedarschema`.

---

## Next Steps

- [Extend Policy Engine](extend-policy-engine.md) for custom entities
- See [Policy API Reference](../reference/api/policy.md) for details
- Review [THREAT_MODEL.md](../THREAT_MODEL.md) for security policies

---

**Last Updated**: 2026-11-18

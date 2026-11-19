# How-To: Extend the Policy Engine

**Goal**: Add custom entity types and context attributes to the Cedar policy engine.

**Time**: 30-45 minutes

**Prerequisites**:
- Understanding of [Cedar Policy Schema](https://docs.cedarpolicy.com/schema/schema.html)
- Familiarity with [Policy API Reference](../reference/api/policy.md)
- Familiarity with [Define Cedar Policies](define-policies.md)

---

## Step 1: Identify New Entity or Attribute Requirements

Define the custom entity or attribute your policies require. Example:

> **Requirement**: Authorize access to a `Repository` entity based on its `visibility` (`"private"` or `"public"`) and `branch_protection` boolean attribute.

---

## Step 2: Add Entity dataclass to `entities.py`

Add the new entity representation in `src/product_value_system/policy/entities.py`:

```python
from dataclasses import dataclass
from typing import Any

from product_value_system.policy.entities import Classification


@dataclass
class Repository:
    """Repository resource entity for Cedar authorization."""

    name: str
    classification: Classification
    visibility: str = "private"
    branch_protection: bool = True

    def to_cedar_entity(self) -> dict[str, Any]:
        """Convert Repository to Cedar JSON entity format."""
        return {
            "type": "ProductValueSystem::Repository",
            "id": self.name,
            "attrs": {
                "name": self.name,
                "classification": self.classification.value,
                "visibility": self.visibility,
                "branchProtection": self.branch_protection,
            },
        }
```

---

## Step 3: Declare the Entity in `policies/schema.cedarschema`

Update `policies/schema.cedarschema` to include `Repository` in the `ProductValueSystem` namespace and add it to the `appliesTo` resource list:

```cedar
namespace ProductValueSystem {
  // ... existing AgentRole, Agent, Tool, Resource declarations ...

  entity Repository = {
    "name": String,
    "classification": String,
    "visibility": String,
    "branchProtection": Bool,
  };

  action read, write, execute, deploy, query, call appliesTo {
    principal: [Agent, AgentRole],
    resource: [Tool, Resource, Repository],
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

## Step 4: Handle Entity Serialization in `PolicyEngine._build_request`

In `src/product_value_system/policy/engine.py`, update `_build_request()` so instances of `Repository` (or any object implementing `to_cedar_entity()`) serialize cleanly:

```python
if hasattr(resource, "to_cedar_entity"):
    resource_entity = resource.to_cedar_entity()
```

---

## Step 5: Add Policy Rules

Add the new policy rules to `policies/agent_authorization.cedar`:

```cedar
// Forbid writing directly to protected repository branches without human approval
forbid(
  principal,
  action == ProductValueSystem::Action::"write",
  resource is ProductValueSystem::Repository
) when {
  resource.branchProtection == true &&
  !context.has_human_approval
};
```

And if using the deterministic fallback evaluator in `PolicyEngine._fallback_evaluate()`, mirror the check so offline/non-CLI environments enforce the same invariant:

```python
resource_type = request["resource"].get("type", "")
resource_attrs = request["resource"].get("attrs", {})

if (
    resource_type == "ProductValueSystem::Repository"
    and action == "write"
    and resource_attrs.get("branchProtection") is True
    and not has_approval
):
    return AuthorizationDecision(
        decision=Decision.DENY,
        reason="Protected repository branch requires human approval for write",
        policies_matched=["policy_forbid_protected_repo_write"],
        timestamp=datetime.now(UTC),
    )
```

---

## Step 6: Write Unit Tests

Create: `tests/unit/test_custom_policy_entities.py`

```python
"""
Unit tests for custom Repository policy entity.
"""

from decimal import Decimal

from product_value_system.policy.engine import Decision, PolicyEngine
from product_value_system.policy.entities import (
    Agent,
    AgentRole,
    AuthorizationContext,
    TrustLevel,
)


def test_custom_entity_dict_passed_to_authorize() -> None:
    """Test passing a custom resource entity dictionary to PolicyEngine.authorize."""
    engine = PolicyEngine()

    agent = Agent(
        id="build-1",
        name="build-agent",
        role=AgentRole(name="build"),
        trust_level=TrustLevel.MEDIUM,
        cost_budget=Decimal("5.0"),
    )
    repo_resource = {
        "type": "Repository",
        "id": "core-payments",
        "attrs": {
            "name": "core-payments",
            "classification": "restricted",
            "visibility": "private",
            "branchProtection": True,
        },
    }
    context = AuthorizationContext(
        environment="sandbox",
        has_human_approval=False,
        cost_so_far=Decimal("0.2"),
        iteration_count=1,
        stage="build",
    )

    decision = engine.authorize(
        principal=agent,
        action="read",
        resource=repo_resource,
        context=context,
    )

    assert decision.decision == Decision.ALLOW
```

Run tests:

```bash
pytest tests/unit/test_custom_policy_entities.py -v
```

---

## Troubleshooting

### `AttributeError: 'Repository' object has no attribute 'to_cedar_entity'`

**Cause**: Missing `to_cedar_entity(self) -> dict[str, Any]` method on custom entity dataclass.

**Solution**: Implement `to_cedar_entity()` returning `{"type": "ProductValueSystem::<Entity>", "id": ..., "attrs": {...}}`.

---

### Policy rule ignored when Cedar CLI is not installed

**Cause**: `PolicyEngine` uses `_fallback_evaluate()` when the `cedar` binary is not on `PATH`.

**Solution**: Ensure new policy conditions are added to both `policies/agent_authorization.cedar` and `PolicyEngine._fallback_evaluate()` in `src/product_value_system/policy/engine.py`.

---

## Next Steps

- [Define Cedar Policies](define-policies.md) for your custom entities
- See [Policy Configuration Reference](../reference/configuration/policies.md)
- See [Policy API Reference](../reference/api/policy.md)

---

**Last Updated**: 2026-11-18

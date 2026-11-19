# API Reference: Policy Engine

## Module: `product_value_system.policy`

**Source**: [`src/product_value_system/policy/`](../../../src/product_value_system/policy/)

---

### Class: `PolicyEngine`

**Source**: [`src/product_value_system/policy/engine.py`](../../../src/product_value_system/policy/engine.py)

Evaluates agent authorization requests against declarative Cedar policies in `policies/agent_authorization.cedar` and schema `policies/schema.cedarschema`.

```python
class PolicyEngine:
    def __init__(self, policies_dir: Path | str = "policies") -> None: ...

    def authorize(
        self,
        principal: Agent | AgentRole | dict[str, Any] | str,
        action: str | dict[str, Any],
        resource: Tool | str | dict[str, Any],
        context: AuthorizationContext | dict[str, Any],
    ) -> AuthorizationDecision: ...
```

---

### Dataclass: `AuthorizationDecision`

- `decision: Decision` (`Decision.ALLOW` or `Decision.DENY`)
- `reason: str` — Human-readable explanation of matched rule or default deny
- `policies_matched: list[str]` — Matched policy identifiers
- `timestamp: datetime` — UTC decision timestamp
- `is_allowed() -> bool` — Convenience boolean check

---

### Entities (`product_value_system.policy.entities`)

**Source**: [`src/product_value_system/policy/entities.py`](../../../src/product_value_system/policy/entities.py)

- `AgentRole(name, trust_level)`
- `Agent(id, role, version, cost_budget, allowed_environments)`
- `Tool(name, classification, risk_level, data_access, requires_approval)`
- `AuthorizationContext(environment, has_human_approval, cost_so_far, iteration_count, stage)`

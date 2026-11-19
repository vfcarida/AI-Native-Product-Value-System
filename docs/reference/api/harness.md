# API Reference: Harness

## Module: `product_value_system.harness`

**Source**: [`src/product_value_system/harness/`](../../../src/product_value_system/harness/)

---

### Class: `Harness`

**Source**: [`src/product_value_system/harness/harness.py`](../../../src/product_value_system/harness/harness.py)

Wraps agent execution with context, policy-gated tools, memory, guardrails, resource budgets, checkpoints, and an immediate kill switch.

```python
class Harness:
    def __init__(
        self,
        context: ExecutionContext | None = None,
        policy_engine: PolicyEngine | None = None,
        budget: Budget | None = None,
        guardrails: Guardrails | None = None,
        checkpoint_dir: Path | None = None,
        model_adapter: ModelAdapter | None = None,
    ) -> None: ...
```

#### Key Methods
- `await invoke_model(messages, tools=None, temperature=0.7, max_tokens=None)` — Invokes the underlying `ModelAdapter` while enforcing iteration and budget constraints.
- `check_constraints(iteration)` — Verifies kill switch, `Budget.check_all()`, and `Guardrails.check_iteration_limit()`.
- `kill()` / `is_killed()` — Immediate kill switch (`REQ-114`).
- `save_checkpoint(iteration, state)` / `load_checkpoint()` — Persists and restores agent state via `CheckpointManager`.

---

### Class: `Budget`

**Source**: [`src/product_value_system/harness/budget.py`](../../../src/product_value_system/harness/budget.py)

Tracks and enforces `max_cost_usd`, `max_iterations`, and `max_duration_seconds` (`REQ-113`). Raises `BudgetExceededError` on violation.

---

### Class: `Guardrails`

**Source**: [`src/product_value_system/harness/guardrails.py`](../../../src/product_value_system/harness/guardrails.py)

Enforces `max_iterations`, regex `blocked_patterns`, and `required_output_schema` JSON Schema validation. Raises `GuardrailViolation` on violation.

---

### Class: `ToolRegistry`

**Source**: [`src/product_value_system/harness/tools.py`](../../../src/product_value_system/harness/tools.py)

Registers tools (`Tool` Protocol) and mediates every invocation through `PolicyEngine.authorize` before calling `await tool.execute(**parameters)`.

---

### Class: `Memory`

**Source**: [`src/product_value_system/harness/memory.py`](../../../src/product_value_system/harness/memory.py)

Manages short-term conversation history (`add_message`, `get_messages`, `clear_messages` with automatic trimming preserving system messages) and long-term artifact storage (`store_artifact`, `retrieve_artifact`).

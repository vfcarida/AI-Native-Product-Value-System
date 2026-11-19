# API Reference: Value Stream Orchestrator

## Module: `product_value_system.orchestrator`

**Source**: [`src/product_value_system/orchestrator/`](../../../src/product_value_system/orchestrator/)

---

### Class: `ValueStreamOrchestrator`

**Source**: [`src/product_value_system/orchestrator/controller.py`](../../../src/product_value_system/orchestrator/controller.py)

Coordinates end-to-end value stream runs across 10 sequential stages, enforcing quality gates, human approval checkpoints, and durable state recovery.

```python
class ValueStreamOrchestrator:
    def __init__(
        self,
        agents: dict[str, Any],
        evaluation_pipeline: EvaluationPipeline | None,
        policy_engine: PolicyEngine | None,
        checkpoint_manager: CheckpointManager,
        artifact_root: Path | str,
        harness_factory: Any | None = None,
    ) -> None: ...

    async def execute_run(
        self,
        problem: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> ValueStreamRun: ...

    async def resume_run(self, run_id: str) -> ValueStreamRun: ...
```

---

### Class: `ValueStreamStateMachine` & `ValueStreamRun`

**Source**: [`src/product_value_system/orchestrator/state_machine.py`](../../../src/product_value_system/orchestrator/state_machine.py)

Defines the 10-stage ordering:
1. `problem_analysis`
2. `outcome_hypothesis`
3. `discovery`
4. `decision_gate`
5. `specification`
6. `review_architecture`
7. `review_security`
8. `build`
9. `quality_gate`
10. `release_decision`

---

### Class: `CheckpointManager`

**Source**: [`src/product_value_system/orchestrator/checkpoints.py`](../../../src/product_value_system/orchestrator/checkpoints.py)

Saves, loads, and deletes JSON run checkpoints (`save_checkpoint`, `load_checkpoint`, `delete_checkpoint`).

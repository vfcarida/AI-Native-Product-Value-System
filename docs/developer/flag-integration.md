# Developer Guide: Integrating Feature Flags (EVO-015)

This guide explains how to evaluate feature flags in application code, wire them into `ValueStreamOrchestrator`, and write deterministic unit and integration tests for flag-gated paths.

---

## Evaluating Flags in Application Code

`FeatureFlagService.is_enabled()` executes in sub-millisecond time (`<1ms`, pure in-memory SHA-256 hashing) and returns a `FlagEvaluation` record containing `enabled`, `reason`, `user_id`, `request_id`, and UTC `timestamp`.

```python
from product_value_system.feature_flags import FeatureFlagService

flags = FeatureFlagService.from_yaml("flags.yaml")

evaluation = flags.is_enabled(
    "new_discovery_prompt",
    user_id="user-123",
    request_context={"request_id": "req-42", "environment": "production"},
)

if evaluation.enabled:
    # Run new code path
    ...
else:
    # Run stable baseline code path
    ...
```

---

## Orchestrator Integration

Pass `feature_flag_service` to `ValueStreamOrchestrator` to automatically gate workflow stages and emit `feature_flag.evaluated` events into `orchestrator.events`:

```python
from product_value_system.feature_flags import FeatureFlagService
from product_value_system.orchestrator.controller import ValueStreamOrchestrator

orchestrator = ValueStreamOrchestrator(
    agents=agents,
    evaluation_pipeline=pipeline,
    policy_engine=policy_engine,
    checkpoint_manager=checkpoint_manager,
    artifact_root=artifact_root,
    feature_flag_service=FeatureFlagService.from_yaml("flags.yaml"),
)

run = await orchestrator.execute_run(
    problem=problem,
    config={
        "user_id": "user-123",
        "environment": "staging",
    },
)
```

Built-in orchestrator flags:

- **`cost_kill_switch`** (`ops`): When enabled, immediately halts runs before executing any agent stage (`OrchestratorError`).
- **`multi_agent_review`** (`release`): Enables parallel 4-agent Elephant-Goldfish review (EVO-013) for matching users/environments unless explicitly overridden by `config["enable_multi_review"]`.
- **`new_discovery_prompt`** (`release`): Evaluated before `outcome_hypothesis` and recorded in `stage_result.metadata["new_discovery_prompt"]` for downstream outcome attribution.

---

## Testing Flagged Code

1. **Test both branches explicitly**: Construct an in-memory `FeatureFlagService({"my_flag": FeatureFlag(...)})` and run assertions with `enabled=True` and `enabled=False`.
2. **Use `tmp_path` for persistence tests**: Copy or write a temporary `flags.yaml` into pytest's `tmp_path` fixture so tests never mutate the repository's root `flags.yaml`.
3. **Avoid testing hash bucket magic numbers directly**: Assert deterministic stability (`is_enabled(u) == is_enabled(u)`) and statistical rollout accuracy across 500–1,000 synthetic user IDs.

---

## Best Practices

- **Default safe (fail closed)**: Unknown flags always evaluate to `enabled=False`.
- **Keep ops flags simple**: Avoid percentage targeting on kill switches like `cost_kill_switch` (`rollout_percentage: 1.0`, toggled via master `enabled` boolean).
- **Never store secrets in `flags.yaml`**: Store only identifiers, environment names, and rollout parameters; `flags.yaml` is version-controlled in Git.
- **Clean up expired flags**: Track `created_at` and retire completed `release`/`experiment` flags promptly to prevent dead branches.

---

## Related Documentation

- [Progressive Delivery Guide](../progressive-delivery.md)
- [Flag Management Guide](../flag-management.md)
- [SLO & Error Budget Guide](../slo-guide.md)

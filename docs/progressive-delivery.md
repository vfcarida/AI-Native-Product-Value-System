# Progressive Delivery Guide (EVO-015)

Progressive delivery decouples **deployment** (shipping code to production) from **release** (exposing features to users). Combined with EVO-014 SLO monitoring, the AI-Native Product Value System rolls out new agent prompts, review pipelines, and workflow capabilities incrementally with automated SLO quality gates and instant rollback.

---

## Progressive Rollout Stages

Every release flag follows a deterministic 6-stage lifecycle managed by `RolloutController`:

| Stage | Traffic % | Purpose | Default Quality Gates |
| :--- | :---: | :--- | :--- |
| `dark` | 0% | Dark launch: code deployed, shadow/staging verification only | `error_rate <= 0%`, `crash_rate <= 0%` |
| `canary` | 5% | Early user cohort + allowlisted beta testers | `error_rate <= 1%`, `quality_score >= 95%` |
| `staged_5` | 5% | Broadened 5% population validation | `error_rate <= 1%`, `quality_score >= 95%` |
| `staged_25` | 25% | Quarter-traffic load & quality verification | `error_rate <= 1%`, `quality_score >= 95%` |
| `staged_50` | 50% | Half-traffic soak test | `error_rate <= 1%`, `quality_score >= 95%` |
| `full` | 100% | General availability (continuous SLO monitoring) | `error_rate <= 1%`, `quality_score >= 95%` |

---

## Creating a Rollout Plan

Use `RolloutPlan` to declare stage durations, quality gates, and automatic rollback behavior:

```python
from product_value_system.feature_flags import (
    FeatureFlagService,
    MetricsSLOMonitor,
    RolloutController,
    RolloutPlan,
)

plan = RolloutPlan(flag_name="multi_agent_review", auto_rollback_enabled=True)
service = FeatureFlagService.from_yaml("flags.yaml")
controller = RolloutController(
    flag_service=service,
    slo_monitor=MetricsSLOMonitor(),
    auto_rollback_enabled=plan.auto_rollback_enabled,
)

# Advance one stage at a time after checking quality gates
result = await controller.advance_rollout("multi_agent_review")
```

---

## Automatic Rollback on SLO Breach

When `auto_rollback_enabled=True` (the default), `RolloutController` evaluates EVO-014 `PVSMetrics` before every stage transition and continuous health check:

1. **Error rate breach**: Exceeding `0%` in `dark` or `1%` in `canary` / `staged_*` / `full` triggers immediate rollback.
2. **Quality degradation**: Evaluation pass rate falling below `95%` triggers immediate rollback.
3. **CRITICAL alerts**: Any active `CRITICAL` alert registered on the SLO monitor triggers immediate rollback.

Rollback sets `flag.enabled = False`, persists `flags.yaml` atomically, records an immutable `AuditEntry`, emits a `rollback_triggered` event, and invokes `alert_callback` to notify the on-call team in seconds (no redeployment required).

---

## Runnable Example

Run the bundled end-to-end demo to see `dark -> canary -> staged_5 -> staged_25 -> staged_50 -> full` followed by an automated SLO breach rollback:

```bash
python examples/progressive_delivery/rollout_demo.py
```

---

## Related Documentation

- [Flag Management Guide](flag-management.md)
- [Developer Flag Integration Guide](developer/flag-integration.md)
- [SLO & Error Budget Guide](slo-guide.md)
- [Alerting Runbook](runbooks/alerting.md)

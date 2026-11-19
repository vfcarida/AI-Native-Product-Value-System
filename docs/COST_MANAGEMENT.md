# Cost Management & Budget Guards

## Overview

The AI-Native Product Value System enforces hard pre-flight and post-call cost budgets to prevent runaway LLM API spending (REQ-113, REQ-114). Every LLM invocation routed through `Harness.invoke_model()` performs pre-flight cost estimation, emergency kill-switch verification, persistent daily/monthly cumulative checks, and threshold alerting.

---

## Budget Enforcement

### Per-Run Budgets (CLI)

```bash
pvs run examples/greenfield/problem_payment_flow.yaml --max-cost 5.0
```

### Daily and Monthly Cumulative Budgets (CLI)

```bash
pvs run examples/greenfield/problem_payment_flow.yaml \
  --max-cost 10.0 \
  --daily-budget 50.0 \
  --monthly-budget 1000.0
```

### Configuration Defaults

Default limits are defined in [config/budget_limits.yaml](../config/budget_limits.yaml):

```yaml
default_limits:
  per_run: 10.0
  daily: 50.0
  monthly: 1000.0

alerts:
  threshold: 0.80

emergency_stop:
  enabled: true
  env_var: EMERGENCY_STOP
```

Load defaults programmatically via:

```python
from product_value_system.harness.budget import Budget

budget = Budget.from_config_file("config/budget_limits.yaml")
```

---

## Emergency Kill Switch

Immediately halt all agent LLM calls and CLI runs by setting `EMERGENCY_STOP=1`:

```bash
export EMERGENCY_STOP=1
pvs run examples/greenfield/problem_payment_flow.yaml
# Exits immediately with code 1 and EmergencyStopTriggered
```

Unset the variable (`unset EMERGENCY_STOP` or `$env:EMERGENCY_STOP=""`) to resume normal operations.

---

## Persistent Time-Window Tracking

Daily and monthly cumulative spend are persisted in SQLite (`~/.pvs/budget.db` by default, or any custom `db_path`):

```bash
sqlite3 ~/.pvs/budget.db "SELECT * FROM daily_spend ORDER BY date DESC LIMIT 7;"
sqlite3 ~/.pvs/budget.db "SELECT * FROM monthly_spend ORDER BY month DESC LIMIT 6;"
```

If the SQLite database encounters a temporary I/O error, `Budget` logs a warning and degrades gracefully without crashing an in-progress agent run.

---

## Cost Alerts & Observability

When projected or actual spend reaches `alert_threshold` (default `80%`), `Budget` triggers once per run:

1. Emits a structured warning log (`Budget alert: 80.0% of budget used ...`)
2. Records an OpenTelemetry span event `budget.alert` with `budget.usage_fraction`, `budget.spent_usd`, and `budget.limit_usd`
3. Invokes an optional `alert_callback(spent_usd, max_usd, usage_fraction)` hook:

```python
from product_value_system.harness.budget import Budget

def notify(spent: float, limit: float, fraction: float) -> None:
    print(f"WARNING: {fraction * 100:.1f}% used (${spent:.2f} / ${limit:.2f})")

budget = Budget(max_usd=10.0, alert_threshold=0.80, alert_callback=notify)
```

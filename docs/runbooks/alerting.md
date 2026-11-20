# Alerting & Incident Response Runbook

Linked from Prometheus alert rules in [`monitoring/prometheus/alerts.yml`](../../monitoring/prometheus/alerts.yml) and routed via [`monitoring/alertmanager/config.yml`](../../monitoring/alertmanager/config.yml).

---

## Multi-Window Burn-Rate Alerts

### `SLOBurnRateFast` (CRITICAL)
- **Trigger**: Both 5m and 1h error rates exceed **10x** the availability error budget.
- **Immediate Actions**:
  1. Check `GET /ready` and inspect `database`, `sandbox`, and `llm` status.
  2. Check OpenTelemetry traces and recent prompt/model config deploys.
  3. If `ProductionMonitor` has not already reverted the release, trigger manual rollback or set `EMERGENCY_STOP=1` (see [Cost Management](../COST_MANAGEMENT.md)).

### `SLOBurnRateSlow` (WARNING)
- **Trigger**: Both 30m and 6h error rates exceed **2x** the availability error budget.
- **Actions**: Review elevated error types in Prometheus (`pvs_errors_total`), check upstream LLM rate limits (HTTP 429), and defer non-critical changes.

### `LatencySLOBurnFast` (CRITICAL) / `LatencySLOBurnSlow` (WARNING)
- **Trigger**: P95 latency exceeds `5.0s` (critical) or trends above `4.0s` (warning).
- **Actions**: Inspect **PVS — Latency & Responsiveness** dashboard, check model adapter timeouts, verify agent iteration limits (`Guardrails.max_iterations`).

---

## AI Quality, Cost, & Safety Alerts

### `QualityDegradationCritical` (CRITICAL) / `QualityDegradationWarning` (WARNING)
- **Trigger**: `pvs_quality_pass_rate` drops below `85%` (critical) or `95%` (warning).
- **Actions**:
  1. Run `pvs eval all --fast --with-trajectories` to isolate the regressed agent.
  2. Compare against `.eval_baselines/` and revert recent prompt or model version updates.

### `CostPerOutcomeOverrun` (WARNING)
- **Trigger**: Average cost per successful outcome exceeds `\$0.50`.
- **Actions**: Check **PVS — LLM Cost & Budget Guards** dashboard, inspect trajectory step counts for redundant tool loops, and confirm `config/budget_limits.yaml` caps.

### `PolicyViolationSpike` (CRITICAL)
- **Trigger**: `>= 5` policy/security violations within 15 minutes.
- **Actions**: Inspect OpenTelemetry `security.prompt_injection` events, run `python scripts/validate-policy-sync.py`, and verify Cedar authorization policies.

---

## Emergency Kill Switch & Rollback

```bash
# Halt all model invocations immediately
export EMERGENCY_STOP=1

# Disable Prometheus metrics export if instrumentation causes overhead
export PVS_ENABLE_METRICS=0
```

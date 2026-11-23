# Production Service Level Objectives (SLOs) & Alerting

## Overview

This document establishes the production **Service Level Objectives (SLOs)**, **Service Level Indicators (SLIs)**, **28-day Error Budgets**, and **Prometheus Alerting Rules** for the AI-Native Product Value System. It complements the in-process Python evaluator ([`docs/slo-guide.md`](../slo-guide.md)) and live `/metrics` + `/slos` endpoints.

All SLO definitions, alert thresholds, and dashboards are managed via GitOps. Existing `/metrics` names (`pvs_requests_total`, `pvs_errors_total`, `pvs_latency_p95_seconds`, `pvs_cost_usd_total`, `pvs_error_budget_remaining`) remain strictly unchanged for backward compatibility.

---

## Defined SLOs (28-Day Rolling Windows)

| ID | SLO Name | Target | Rolling Window | Error Budget | Allowable Downtime / Failures (28d) |
|---|---|---|---|---:|---|
| **SLO-01** | **API Availability** | `>= 99.9%` | `28d` (`5m` SLI) | `0.1%` (`0.001`) | `40.32 minutes` per 28 days (`8.76h / year`) |
| **SLO-02** | **API Latency (p99)** | `99%` `< 1000ms` | `28d` (`5m` SLI) | `1.0%` (`0.01`) | `403.2 minutes` per 28 days |
| **SLO-03** | **API 5xx Error Rate** | `< 1%` (`5xx`) | `28d` (`5m` SLI) | `0.5%` (`0.005`) | Half of `1%` threshold |
| **SLO-04** | **Agent Completion Rate** | `> 95%` | `28d` (`10m` SLI) | `2.0%` (`0.02`) | `3%` failure allowance + `2%` operational buffer |

---

### SLO-01: API Availability (Uptime)
- **Target**: `99.9%` (`0.999`) — equivalent to at most `8.76 hours` downtime per year.
- **Window**: `28 days` rolling (`5m` measurement interval).
- **Error Budget**: `0.1%` (`40.32 minutes` per 28 days).
- **Rationale**: Industry-standard reliability target for critical control-plane APIs serving autonomous agent workflows.
- **SLI (PromQL)**:
```promql
sum(rate(http_requests_total{code=~"2.."}[5m]))
/
clamp_min(sum(rate(http_requests_total[5m])), 1e-9)
```

### SLO-02: API Latency (p99)
- **Target**: `99%` of HTTP requests complete in `< 1000ms` (`1.0s`).
- **Window**: `28 days` rolling (`5m` measurement interval).
- **Error Budget**: `1.0%` (`403.2 minutes` per 28 days).
- **Rationale**: Protects interactive CLI/API responsiveness and prevents upstream client timeouts while allowing slower async LLM invocations to be tracked separately via `pvs_latency_p95_seconds`.
- **SLI (PromQL)**:
```promql
histogram_quantile(
  0.99,
  sum(rate(http_request_duration_seconds_bucket[5m])) by (le)
) < 1.0
```

### SLO-03: API Error Rate (5xx)
- **Target**: `< 1%` (`0.01`) of HTTP requests return `5xx` server errors.
- **Window**: `28 days` rolling (`5m` measurement interval).
- **Error Budget**: `0.5%` (`0.005` — half of the `1%` ceiling).
- **Rationale**: Distinguishes true server-side faults (`5xx`) from expected client/authorization rejections (`4xx`).
- **SLI (PromQL)**:
```promql
sum(rate(http_requests_total{code=~"5.."}[5m]))
/
clamp_min(sum(rate(http_requests_total[5m])), 1e-9)
```

### SLO-04: Agent Completion Success Rate
- **Target**: `> 95%` (`0.95`) of agent runs reach `status="completed"`.
- **Window**: `28 days` rolling (`10m` measurement interval).
- **Error Budget**: `2.0%` (`0.02`, allowing up to `3%` transient LLM/sandbox errors with a `2%` operational buffer).
- **Rationale**: Measures end-to-end product value delivery across Discovery, Specification, Multi-Agent Review, and Build stages.
- **SLI (PromQL)**:
```promql
sum(rate(agent_runs_total{status="completed"}[10m]))
/
clamp_min(sum(rate(agent_runs_total[10m])), 1e-9)
```

---

## Error Budget Policy

Error budgets govern deployment velocity and reliability investment:

1. **Healthy (`> 50%` remaining)**: Normal feature deployments, prompt iterations, and progressive canary rollouts proceed.
2. **Caution (`25%` – `50%` remaining)**: Prioritize reliability hardening, flaky test elimination, and provider fallback tuning; review in weekly SRE sync.
3. **Exhausted (`0%` remaining)**:
   - **Freeze feature deployments** immediately (reliability/security bug fixes and rollbacks only).
   - **Root Cause Analysis (RCA)** required before resuming releases.
   - **Blameless Postmortem** published within **48 hours** with concrete prevention action items.
   - **Resume feature deployments** only after the rolling error budget recovers to **> 25%**.

**Governance Cadence**:
- **Weekly SRE Review**: Review 28-day error budget burn, top alert drivers, and postmortem actions.
- **GitOps Audit Trail**: All SLO threshold changes require Pull Request review in Git; Alertmanager retains alert firing history for **90 days**; error budget consumption is recorded daily.

---

## Alerting Rules

Kubernetes Prometheus Operator rules are defined in [`deploy/helm/product-value-system/templates/prometheusrule.yaml`](../../deploy/helm/product-value-system/templates/prometheusrule.yaml) (configured via `slo.*` in [`deploy/helm/product-value-system/values.yaml`](../../deploy/helm/product-value-system/values.yaml)) alongside local Docker Compose rules in [`monitoring/prometheus/alerts.yml`](../../monitoring/prometheus/alerts.yml).

### Summary of Deployed Rules (12 Alerts + 4 Recording Rules)

| Group | Alert Name | Condition | `for` | Severity | Runbook |
|---|---|---|---|---|---|
| `slo_availability` | `SLOAvailabilityBudgetBurn` | Non-2xx rate `> 0.1%` | `5m` | `warning` | [Alerting Runbook](../runbooks/alerting.md#sloburnrateslow) |
| `slo_availability` | `SLOAvailabilityCritical` | Non-2xx rate `> 1.0%` (10x budget) | `2m` | `critical` | [Alerting Runbook](../runbooks/alerting.md#sloburnratefast) |
| `slo_availability` | `SLOAvailabilityMultiWindowBurn` | `pvs_requests_total` 5m & 1h burn `> 14.4x` | `2m` | `critical` | [Alerting Runbook](../runbooks/alerting.md#sloburnratefast) |
| `slo_latency` | `SLOLatencyP99High` | HTTP p99 `> 1.0s` | `5m` | `warning` | [Alerting Runbook](../runbooks/alerting.md#latencysloburnslow) |
| `slo_latency` | `SLOLatencyP99Critical` | HTTP p99 `> 5.0s` | `2m` | `critical` | [Alerting Runbook](../runbooks/alerting.md#latencysloburnfast) |
| `slo_latency` | `SLOLatencyP95Sustained` | `pvs_latency_p95_seconds` 15m avg `> 5.0s` | `10m` | `warning` | [Alerting Runbook](../runbooks/alerting.md#latencysloburnslow) |
| `slo_error_rate` | `SLOErrorRateHigh` | HTTP 5xx rate `> 1%` | `5m` | `warning` | [Alerting Runbook](../runbooks/alerting.md#sloburnrateslow) |
| `slo_error_rate` | `SLOErrorRateCritical` | HTTP 5xx rate `> 5%` | `2m` | `critical` | [Alerting Runbook](../runbooks/alerting.md#sloburnratefast) |
| `slo_agent_success` | `AgentSuccessRateLow` | Agent completion `< 95%` | `10m` | `warning` | [Alerting Runbook](../runbooks/alerting.md#qualitydegradationwarning) |
| `slo_agent_success` | `AgentSuccessRateCritical` | Agent completion `< 85%` | `5m` | `critical` | [Alerting Runbook](../runbooks/alerting.md#qualitydegradationcritical) |
| `resource_saturation` | `PodMemoryHigh` | Container memory `> 90%` limit | `5m` | `warning` | [Alerting Rules](#alerting-rules) |
| `resource_saturation` | `PodCPUThrottled` | CPU CFS throttled `> 50%` | `5m` | `warning` | [Alerting Rules](#alerting-rules) |

### Alert Severity SLAs

- **Critical**: Immediate on-call page (PagerDuty / Opsgenie), **< 15 minute** response SLA.
- **Warning**: Slack `#pvs-alerts` notification, **< 1 hour** triage SLA during business hours.
- **Info**: Dashboard visibility only; reviewed in the next standup / weekly SRE review.

### Low-Cardinality Label Guarantee

Alert labels are strictly restricted to bounded keys (`severity`, `slo`, `tier`). High-cardinality identifiers (`run_id`, `user_id`, `request_id`, `trace_id`) are never attached to Prometheus metrics or alerting rules.

---

## Deployment & Grafana Dashboard

### Deploy via Helm (Kubernetes)

```bash
helm upgrade --install pvs deploy/helm/product-value-system \
  --namespace product-value-system --create-namespace \
  --set secrets.existingSecret=pvs-secrets \
  --set serviceMonitor.enabled=true \
  --set slo.enabled=true \
  --set grafanaDashboards.enabled=true
```

### Verify Rules & Import Grafana Dashboard

```bash
# Confirm PrometheusRule CRD in cluster
kubectl get prometheusrules -n product-value-system

# Import standalone Grafana SLO dashboard
jq '{dashboard: ., overwrite: true}' grafana/dashboards/slo-dashboard.json | \
  curl -X POST http://admin:admin@localhost:3000/api/dashboards/db \
    -H "Content-Type: application/json" -d @-
```

Standalone dashboard JSON: [`grafana/dashboards/slo-dashboard.json`](../../grafana/dashboards/slo-dashboard.json).

---

See also: [In-Process SLO Guide](../slo-guide.md) · [Alerting Runbook](../runbooks/alerting.md) · [Monitoring Operations](monitoring.md) · [Helm Chart Guide](../../deploy/helm/product-value-system/README.md)

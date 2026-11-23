# Production SLO & Error Budget Guide

This guide defines the **Service Level Indicators (SLIs)**, **Service Level Objectives (SLOs)**, and **Error Budget Policies** implemented in [`src/product_value_system/monitoring/`](../src/product_value_system/monitoring/). For Kubernetes 28-day SLO targets, Prometheus Operator `PrometheusRule` alerts, and Grafana dashboards, see [`docs/operations/slos.md`](operations/slos.md).

---

## Service Level Indicators (SLIs)

### Traditional SLIs (5)
1. **Availability (`availability`)**: Ratio of successful requests to total requests (`30d` rolling window).
2. **Latency (`latency_p95`)**: 95th percentile agent/model invocation latency in seconds (`7d`).
3. **Error Rate (`error_rate`)**: Proportion of requests ending in unhandled errors (`7d`).
4. **Throughput (`throughput`)**: Completed agent executions per minute (`1h`).
5. **Saturation (`saturation`)**: Active concurrent runs relative to worker capacity (`1h`).

### AI-Specific SLIs (3 + Safety)
6. **Quality Score (`quality_score`)**: Ratio of runs passing evaluation gates (`7d`).
7. **Cost per Outcome (`cost_per_outcome`)**: Average USD LLM + agent cost per completed outcome (`7d`).
8. **Hallucination-Free Rate (`hallucination_free_rate`)**: `1 - (hallucinated facts / total claims)` (`7d`).
9. **Safety Compliance (`safety_compliance`)**: `1 - (policy violations / total executions)` (`30d`).

---

## Default Production SLO Targets

| SLO Name | Target | Window | Error Budget |
|----------|--------|--------|--------------|
| `availability` | `>= 99.0%` (`0.99`) | 30d | `1.0%` (`0.01`) |
| `latency_p95` | `<= 5.0s` | 7d | `+5.0s` headroom (`max 10.0s`) |
| `error_rate` | `<= 1.0%` (`0.01`) | 7d | `+4.0%` headroom (`max 5.0%`) |
| `quality_score` | `>= 95.0%` (`0.95`) | 7d | `5.0%` (`0.05`) |
| `cost_per_outcome` | `<= $0.50` | 7d | `+$0.50` headroom (`max $1.00`) |
| `hallucination_free_rate` | `>= 98.0%` (`0.98`) | 7d | `2.0%` (`0.02`) |
| `safety_compliance` | `>= 99.5%` (`0.995`) | 30d | `0.5%` (`0.005`) |

---

## Error Budget Calculation & Policy

For ratio SLOs (`availability`, `quality_score`, `hallucination_free_rate`):

- **Error Budget** = $1 - \text{target}$
- **Budget Remaining** = $\max\left(0,\; 1 - \frac{\text{target} - \text{actual}}{1 - \text{target}}\right)$

Example: Target = `0.99`, Actual = `0.985` → `0.005 / 0.01` = 50% consumed → **50% remaining**.

### Error Budget Policy Actions

| Budget Remaining | Operational Policy Action |
|------------------|---------------------------|
| `>= 50%` (`0.50`) | Continue normal feature deployments and prompt experiments |
| `> 0%` to `< 50%` (`0.25` band) | Suspend non-critical changes; prioritize reliability and quality fixes |
| `0%` (`0.00`) | Freeze all releases except emergency fixes; trigger automated rollback via `ProductionMonitor` |

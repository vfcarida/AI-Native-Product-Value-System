# Grafana Dashboards Guide

Four pre-provisioned Grafana dashboards live in [`monitoring/grafana/dashboards/`](../monitoring/grafana/dashboards/) and load automatically when running [`docker-compose.monitoring.yml`](../docker-compose.monitoring.yml).

---

## Accessing Grafana

1. Start the monitoring stack:
   ```bash
   docker compose -f docker-compose.monitoring.yml up -d
   ```
2. Open Grafana at `http://localhost:3000` (configured via `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD`).
3. Navigate to **Dashboards → Product Value System**.

---

## Dashboard Catalog

| Dashboard | File | Panels |
|-----------|------|--------|
| **Latency & Responsiveness** | [`latency.json`](../monitoring/grafana/dashboards/latency.json) | P50/P95/P99 time series, latency heatmap, throughput table by status, latency SLO compliance gauge |
| **LLM Cost & Budget Guards** | [`cost.json`](../monitoring/grafana/dashboards/cost.json) | Cumulative USD cost curve, cost by agent pie chart, cost per outcome bar chart, cost SLO budget gauge |
| **AI Output Quality & Safety** | [`quality.json`](../monitoring/grafana/dashboards/quality.json) | Eval pass rate & hallucination-free time series, quality histogram, policy violations table, quality SLO stat |
| **SLO Error Budgets & Burn Rates** | [`error_budget.json`](../monitoring/grafana/dashboards/error_budget.json) | Remaining error budget gauge per SLO, 1h availability burn-rate multiplier, active runs saturation, policy gate indicator |

---

## Live JSON & Prometheus Endpoints

- `GET /health` — Process liveness probe
- `GET /ready` — Dependency readiness check (`database`, `sandbox`, `llm`)
- `GET /metrics` — Prometheus scrape endpoint (set `PVS_METRICS_API_KEY` to require `X-API-Key` / Bearer auth)
- `GET /slos` — Live JSON summary of every SLO's compliance and remaining error budget

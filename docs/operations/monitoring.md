# Monitoring Operations Guide

Operational guide for deploying and maintaining the PVS Prometheus, Alertmanager, and Grafana stack.

---

## 1. Local & Staging Deployment

```bash
# Install monitoring extras (FastAPI, Uvicorn, Prometheus exporter)
pip install -e ".[monitoring]"

# Start the health/metrics HTTP service locally
uvicorn product_value_system.api.health:app --host 0.0.0.0 --port 8000

# Or start the full Prometheus + Alertmanager + Grafana stack via Docker Compose
docker compose -f docker-compose.monitoring.yml up -d
```

---

## 2. Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `PVS_ENABLE_METRICS` | `1` | Set to `0` to disable OpenTelemetry/Prometheus metrics recording |
| `PVS_METRICS_API_KEY` | *(unset)* | Require `X-API-Key` or `Authorization: Bearer <key>` on `GET /metrics` |
| `PAGERDUTY_ROUTING_KEY` | *(secret)* | Alertmanager routing key for critical alerts |
| `SLACK_CRITICAL_WEBHOOK_URL` | *(secret)* | Slack webhook for `#pvs-critical` |
| `SLACK_WARNINGS_WEBHOOK_URL` | *(secret)* | Slack webhook for `#pvs-alerts` |

---

## 3. Cardinality & Security Hygiene

- Keep metric labels strictly low-cardinality (`agent`, `status`, `model`, `slo`, `artifact_type`). Never attach `run_id`, `user_id`, or raw prompts as metric labels.
- Restrict `/metrics` to internal scrape networks and enable `PVS_METRICS_API_KEY` outside local development.

---

## 4. Adding a New Metric & SLO

1. Register a new `SLI` in [`src/product_value_system/monitoring/sli.py`](../../src/product_value_system/monitoring/sli.py) and `SLO` in [`src/product_value_system/monitoring/slo.py`](../../src/product_value_system/monitoring/slo.py).
2. Add the OpenTelemetry instrument and snapshot field in [`src/product_value_system/monitoring/metrics.py`](../../src/product_value_system/monitoring/metrics.py).
3. Map the new `SLIType` inside `SLOEvaluator.compute_compliance()` ([`slo_evaluator.py`](../../src/product_value_system/monitoring/slo_evaluator.py)).
4. Add alert rules to [`monitoring/prometheus/alerts.yml`](../../monitoring/prometheus/alerts.yml) and corresponding runbook entry in [`docs/runbooks/alerting.md`](../runbooks/alerting.md).

# Alert Runbook Index

Complete mapping of all Prometheus SLO and infrastructure alerts defined in [prometheusrule.yaml](../../deploy/helm/product-value-system/templates/prometheusrule.yaml) to severity, routing channels, MTTR targets, and runbook procedures.

---

## Active Alert Catalog

| Alert Name | Group | Severity | Delivery Channel | MTTR Target | Runbook |
|---|---|---|---|---|---|
| `SLOAvailabilityCritical` | `slo_availability` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [SLO Burn Rate Fast](../runbooks/alerting.md#sloburnratefast) |
| `SLOAvailabilityMultiWindowBurn` | `slo_availability` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [SLO Burn Rate Fast](../runbooks/alerting.md#sloburnratefast) |
| `SLOAvailabilityBudgetBurn` | `slo_availability` | Warning | `#alerts-warnings` | 1 hour | [SLO Burn Rate Slow](../runbooks/alerting.md#sloburnrateslow) |
| `SLOLatencyP99Critical` | `slo_latency` | **Critical** | PagerDuty + `#alerts-critical` | 30 min | [High-Latency Trace Runbook](../runbooks/trace-high-latency.md) |
| `SLOLatencyP99High` | `slo_latency` | Warning | `#alerts-warnings` | 1 hour | [High-Latency Trace Runbook](../runbooks/trace-high-latency.md) |
| `SLOLatencyP95Sustained` | `slo_latency` | Warning | `#alerts-warnings` | 1 hour | [High-Latency Trace Runbook](../runbooks/trace-high-latency.md) |
| `SLOErrorRateCritical` | `slo_error_rate` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [SLO Burn Rate Fast](../runbooks/alerting.md#sloburnratefast) |
| `SLOErrorRateHigh` | `slo_error_rate` | Warning | `#alerts-warnings` | 1 hour | [SLO Burn Rate Slow](../runbooks/alerting.md#sloburnrateslow) |
| `AgentSuccessRateCritical` | `slo_agent_success` | **Critical** | PagerDuty + `#alerts-critical` | 30 min | [Quality Degradation Critical](../runbooks/alerting.md#qualitydegradationcritical) |
| `AgentSuccessRateLow` | `slo_agent_success` | Warning | `#alerts-warnings` | 1 hour | [Quality Degradation Warning](../runbooks/alerting.md#qualitydegradationwarning) |
| `PodMemoryHigh` | `resource_saturation` | Warning | `#alerts-warnings` | 1 hour | [SLO Alerting Rules](slos.md#alerting-rules) |
| `PodCPUThrottled` | `resource_saturation` | Warning | `#alerts-warnings` | 1 hour | [SLO Alerting Rules](slos.md#alerting-rules) |
| `BackupFailed` | `backup_health` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [Disaster Recovery Plan](disaster-recovery-plan.md) |
| `BackupOld` | `backup_health` | Warning | `#alerts-warnings` | 1 hour | [Disaster Recovery Plan](disaster-recovery-plan.md) |
| `BackupSizeTooSmall` | `backup_health` | Warning | `#alerts-warnings` | 1 hour | [Restore Procedures](restore-procedures.md) |

---

## Security & Privacy Guardrails for Alerts

- **No Secrets or PII in Notifications**: Never interpolate raw prompts, API keys, passwords, or email addresses into Prometheus labels or annotations (`sanitize_alert_payload()` enforces redaction in `AlertRoutingEngine`).
- **Low-Cardinality Labels**: Alert rules use bounded labels (`severity`, `slo`, `tier`, `team`) only — never `user_id`, `run_id`, or `trace_id`.

---

## Authoring a New Alert Runbook

1. Add a runbook markdown guide under `docs/runbooks/` containing **Trigger**, **Investigation**, **Mitigation**, and **Verification** sections.
2. Attach both `runbook_url` and `dashboard_url` annotations to the alert rule in `deploy/helm/product-value-system/templates/prometheusrule.yaml`.
3. Register the alert in the table above and verify with `pytest tests/alerting/ tests/monitoring/ -v`.

---

## Related Documentation

- [On-Call Handbook](oncall-guide.md)
- [Production SLOs & Alerting Rules](slos.md)
- [Alert Metrics Grafana Dashboard](../../grafana/dashboards/alert-metrics.json)

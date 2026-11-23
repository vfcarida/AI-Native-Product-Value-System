# Alert Runbook Index

Complete mapping of all Prometheus SLO and infrastructure alerts defined in [prometheusrule.yaml](../../deploy/helm/product-value-system/templates/prometheusrule.yaml) to severity, routing channels, MTTR targets, and runbook procedures.

---

## Active Alert Catalog

| Alert Name | Group | Severity | Delivery Channel | MTTR Target | Runbook |
|---|---|---|---|---|---|
| `SLOAvailabilityCritical` | `slo_availability` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [API Unavailable](../runbooks/incident-api-unavailable.md) |
| `SLOAvailabilityMultiWindowBurn` | `slo_availability` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [API Unavailable](../runbooks/incident-api-unavailable.md) |
| `SLOAvailabilityBudgetBurn` | `slo_availability` | Warning | `#alerts-warnings` | 1 hour | [API Unavailable](../runbooks/incident-api-unavailable.md) |
| `SLOLatencyP99Critical` | `slo_latency` | **Critical** | PagerDuty + `#alerts-critical` | 30 min | [High API Latency](../runbooks/incident-high-latency.md) |
| `SLOLatencyP99High` | `slo_latency` | Warning | `#alerts-warnings` | 1 hour | [High API Latency](../runbooks/incident-high-latency.md) |
| `SLOLatencyP95Sustained` | `slo_latency` | Warning | `#alerts-warnings` | 1 hour | [High API Latency](../runbooks/incident-high-latency.md) |
| `SLOErrorRateCritical` | `slo_error_rate` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [High Error Rate](../runbooks/incident-high-error-rate.md) |
| `SLOErrorRateHigh` | `slo_error_rate` | Warning | `#alerts-warnings` | 1 hour | [High Error Rate](../runbooks/incident-high-error-rate.md) |
| `AgentSuccessRateCritical` | `slo_agent_success` | **Critical** | PagerDuty + `#alerts-critical` | 30 min | [Agent Success Drop](../runbooks/incident-agent-success-drop.md) |
| `AgentSuccessRateLow` | `slo_agent_success` | Warning | `#alerts-warnings` | 1 hour | [Agent Success Drop](../runbooks/incident-agent-success-drop.md) |
| `PodMemoryHigh` | `resource_saturation` | Warning | `#alerts-warnings` | 1 hour | [Pod OOM Kill & High Memory](../runbooks/incident-oom-kill.md) |
| `PodCPUThrottled` | `resource_saturation` | Warning | `#alerts-warnings` | 1 hour | [High CPU Usage & Throttling](../runbooks/incident-high-cpu-usage.md) |
| `BackupFailed` | `backup_health` | **Critical** | PagerDuty + `#alerts-critical` | 15 min | [Database Backup Failure](../runbooks/incident-backup-failure.md) |
| `BackupOld` | `backup_health` | Warning | `#alerts-warnings` | 1 hour | [Database Backup Failure](../runbooks/incident-backup-failure.md) |
| `BackupSizeTooSmall` | `backup_health` | Warning | `#alerts-warnings` | 1 hour | [Database Backup Failure](../runbooks/incident-backup-failure.md) |

For the complete catalog of all **20 incident runbooks**, **6 operational playbooks**, and **4 automation scripts**, see [On-Call Quick Reference](../runbooks/oncall-quick-reference.md) and [Troubleshooting Flowchart](../runbooks/troubleshooting-flowchart.md).

---

## Security & Privacy Guardrails for Alerts

- **No Secrets or PII in Notifications**: Never interpolate raw prompts, API keys, passwords, or email addresses into Prometheus labels or annotations (`sanitize_alert_payload()` enforces redaction in `AlertRoutingEngine`).
- **Low-Cardinality Labels**: Alert rules use bounded labels (`severity`, `slo`, `tier`, `team`) only — never `user_id`, `run_id`, or `trace_id`.

---

## Authoring a New Alert Runbook

1. Copy [.github/RUNBOOK_TEMPLATE.md](../../.github/RUNBOOK_TEMPLATE.md) into `docs/runbooks/incident-<name>.md` and populate all 7 sections (**Symptoms**, **Diagnosis**, **Remediation**, **Prevention**, **Escalation**, **References**, **Testing**).
2. Attach both `runbook_url` and `dashboard_url` annotations to the alert rule in `deploy/helm/product-value-system/templates/prometheusrule.yaml`.
3. Register the alert in the table above and verify with `pytest tests/operations/ tests/alerting/ tests/monitoring/ -v`.

---

## Related Documentation

- [On-Call Quick Reference](../runbooks/oncall-quick-reference.md)
- [Troubleshooting Flowchart](../runbooks/troubleshooting-flowchart.md)
- [On-Call Handbook](oncall-guide.md)
- [Production SLOs & Alerting Rules](slos.md)
- [Alert Metrics Grafana Dashboard](../../grafana/dashboards/alert-metrics.json)

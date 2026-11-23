# On-Call Engineer Quick Reference

Single-page operational cheat sheet for first-60-seconds incident triage, runbook lookup, automation commands, and FAQ.

---

## 1. First 60 Seconds Checklist

1. **Acknowledge** PagerDuty alert (`< 5 min`) and post `"Investigating <ID>"` in `#incidents`.
2. **Check Cluster & Probes**:
   ```bash
   kubectl get pods,hpa,pdb -n product-value-system
   kubectl exec -n product-value-system deploy/product-value-system -- curl -fsS http://localhost:8000/health/ready
   ```
3. **Open Runbook**: Follow `runbook_url` in the alert or use [Troubleshooting Flowchart](troubleshooting-flowchart.md).

---

## 2. Automation Scripts

| Script | Purpose | Command |
|---|---|---|
| [auto-scale-up.sh](../../scripts/runbook-automation/auto-scale-up.sh) | Scale app replicas up to HPA max | `./scripts/runbook-automation/auto-scale-up.sh --increment 2` |
| [restart-unhealthy-pods.sh](../../scripts/runbook-automation/restart-unhealthy-pods.sh) | Save previous logs & recycle crashing pods | `./scripts/runbook-automation/restart-unhealthy-pods.sh` |
| [clear-cache.sh](../../scripts/runbook-automation/clear-cache.sh) | Non-blocking Redis prompt cache flush | `./scripts/runbook-automation/clear-cache.sh --yes` |
| [diagnose-slow-queries.sh](../../scripts/runbook-automation/diagnose-slow-queries.sh) | List queries `> 100ms` & missing indexes | `./scripts/runbook-automation/diagnose-slow-queries.sh` |
| [silence-alert.sh](../../scripts/silence-alert.sh) | Create maintenance silence | `./scripts/silence-alert.sh --duration 2h --comment "Maint"` |
| [pitr-recovery.sh](../../scripts/pitr-recovery.sh) | Point-in-time DB recovery | `./scripts/pitr-recovery.sh "YYYY-MM-DD HH:MM:SS"` |

---

## 3. Complete 20-Runbook Directory

| # | Incident Runbook | Primary Alert / Trigger |
|---|---|---|
| 1 | [incident-high-latency.md](incident-high-latency.md) | `SLOLatencyP99High`, `SLOLatencyP99Critical`, `SLOLatencyP95Sustained` |
| 2 | [incident-high-error-rate.md](incident-high-error-rate.md) | `SLOErrorRateHigh`, `SLOErrorRateCritical` |
| 3 | [incident-api-unavailable.md](incident-api-unavailable.md) | `SLOAvailabilityCritical`, `SLOAvailabilityMultiWindowBurn`, `SLOAvailabilityBudgetBurn` |
| 4 | [incident-agent-success-drop.md](incident-agent-success-drop.md) | `AgentSuccessRateLow`, `AgentSuccessRateCritical` |
| 5 | [incident-database-connection-exhaustion.md](incident-database-connection-exhaustion.md) | PgBouncer / pool saturation |
| 6 | [incident-database-primary-down.md](incident-database-primary-down.md) | Patroni leader crash / failover |
| 7 | [incident-slow-queries.md](incident-slow-queries.md) | `pg_stat_statements` `> 100ms` |
| 8 | [incident-replication-lag.md](incident-replication-lag.md) | PostgreSQL standby lag `> 1MB` |
| 9 | [incident-pod-crashloop.md](incident-pod-crashloop.md) | `CrashLoopBackOff` |
| 10 | [incident-oom-kill.md](incident-oom-kill.md) | `PodMemoryHigh`, Exit Code `137` |
| 11 | [incident-high-cpu-usage.md](incident-high-cpu-usage.md) | `PodCPUThrottled` |
| 12 | [incident-disk-full.md](incident-disk-full.md) | PVC / disk utilization `>= 90%` |
| 13 | [incident-llm-provider-outage.md](incident-llm-provider-outage.md) | OpenAI / Anthropic / Gemini 5xx |
| 14 | [incident-llm-rate-limit.md](incident-llm-rate-limit.md) | Upstream HTTP `429` throttling |
| 15 | [incident-budget-exhaustion.md](incident-budget-exhaustion.md) | FinOps budget cap / `EMERGENCY_STOP` |
| 16 | [incident-cache-degradation.md](incident-cache-degradation.md) | Redis outage / cache miss spike |
| 17 | [incident-vault-sealed.md](incident-vault-sealed.md) | Vault sealed / dynamic lease error |
| 18 | [incident-sandbox-egress-block.md](incident-sandbox-egress-block.md) | `iptables` allowlist denial |
| 19 | [incident-backup-failure.md](incident-backup-failure.md) | `BackupFailed`, `BackupOld`, `BackupSizeTooSmall` |
| 20 | [incident-audit-tamper-alert.md](incident-audit-tamper-alert.md) | HMAC verification failure / `AUDIT_EMERGENCY` |

---

## 4. Common Failure Modes FAQ

- **Why did a deploy stall with 1 pod pending?** Check `PodDisruptionBudget` (`minAvailable: 2`) or node CPU/memory quotas (`kubectl describe pod`).
- **Why did `asyncpg` throw `DuplicatePreparedStatementError`?** Confirm `DATABASE_STATEMENT_CACHE_SIZE=0` when routing through PgBouncer `pool_mode = transaction`.
- **How do I immediately stop LLM spend during a runaway loop?** Set `EMERGENCY_STOP=true` in the deployment ConfigMap/Env and roll pods ([Budget Exhaustion](incident-budget-exhaustion.md)).

---

## 5. Escalation & Core Procedures

- **Deployment / Rollback**: [Deployment Checklist](deployment-checklist.md) · [Rollback Procedure](rollback-procedure.md)
- **Emergencies**: [Emergency Data Loss](emergency-data-loss.md) · [Emergency Security Breach](emergency-security-breach.md)
- **Handbook**: [On-Call Guide](../operations/oncall-guide.md) (`0m` Primary -> `15m` Backup -> `30m` Engineering Manager)

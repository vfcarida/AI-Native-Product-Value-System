# Runbook: Slow Database Queries

**Severity**: P2 (Warning) / P1 (if causing p99 > 5s)  
**Alert**: `SLOLatencyP99High` / `pg_stat_statements` mean time `> 100ms`  
**SLO Impact**: SLO-02 Latency  
**Estimated MTTR**: 15–25 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Database Engineering

---

## 1. Symptoms
- [ ] Elevated API p95/p99 latency with database spans dominating Jaeger traces
- [ ] Rising PostgreSQL CPU utilization
- [ ] `pg_stat_statements` reports `mean_exec_time > 100ms`

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
./scripts/runbook-automation/diagnose-slow-queries.sh --threshold-ms 100
```

### Decision Tree
- `Seq Scan` on large table (`audit_logs`, `stages`, `evaluations`) -> Section 3.A
- Lock contention (`wait_event_type = 'Lock'`) -> Section 3.B

---

## 3. Remediation

### 3.A: Add Missing Non-Blocking Index & Refresh Statistics
```bash
# Always use CONCURRENTLY to avoid blocking production writes
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- \
  psql -U pvs_user -d pvs_db -c "ANALYZE;"
# Create targeted index using CONCURRENTLY after verifying EXPLAIN ANALYZE
```

### 3.B: Cancel Runaway Analytical Query
```bash
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- \
  psql -U postgres -d pvs_db -c "SELECT pg_cancel_backend(<PID>);"
```
**Rollback**: Drop newly added index via `DROP INDEX CONCURRENTLY` if write amplification occurs.

---

## 4. Prevention
- [ ] Verify composite indexes exist on `audit_logs` (`migrations/postgres/002_create_audit_tables.sql`)
- [ ] Keep 90-day hot archival CronJob active (`cronjob-archive.yaml`) to bound table size

---

## 5. Escalation
Escalate to DBA Lead after 20 minutes with `EXPLAIN ANALYZE` plan and `diagnose-slow-queries.sh` output.

---

## 6. References
- [High Latency Runbook](incident-high-latency.md)
- [Audit Retention Policy](../compliance/retention-policy.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `./scripts/runbook-automation/diagnose-slow-queries.sh --dry-run` and validate index presence in `migrations/postgres/002_create_audit_tables.sql`.

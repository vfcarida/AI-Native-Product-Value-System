# Runbook: Database Connection Pool Exhaustion

**Severity**: P1 (Critical)  
**Alert**: `SLOLatencyP99High` / PgBouncer `cl_waiting > 0`  
**SLO Impact**: SLO-02 Latency & SLO-03 Error Rate  
**Estimated MTTR**: 10–15 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Database / SRE Team

---

## 1. Symptoms
- [ ] `asyncpg` or PgBouncer timeouts waiting for available connections
- [ ] `/health/metrics-summary` shows `database_pool.checked_out` near `max_size`
- [ ] Rising p99 latency and HTTP 503 readiness failures

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect PgBouncer active pools & waiting clients
kubectl exec -n product-value-system deploy/product-value-system-pgbouncer -- \
  psql -h localhost -p 5432 -U pvs_user -d pgbouncer -c "SHOW POOLS;"

# 2. Check idle-in-transaction sessions on PostgreSQL primary
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- \
  psql -U pvs_user -d pvs_db -c "SELECT pid, state, now() - state_change AS age, left(query, 80) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY age DESC LIMIT 10;"
```

### Decision Tree
- Long-running `idle in transaction` sessions blocking slots -> Section 3.A
- Legitimate traffic surge exhausting `default_pool_size` -> Section 3.B

---

## 3. Remediation

### 3.A: Terminate Stuck `idle in transaction` Sessions (> 5 min)
```bash
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- \
  psql -U postgres -d pvs_db -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' AND now() - state_change > interval '5 minutes';"
```

### 3.B: Increase PgBouncer Pool Capacity
```bash
helm upgrade product-value-system deploy/helm/product-value-system \
  --reuse-values --set pgbouncer.defaultPoolSize=40 --set pgbouncer.replicas=3 -n product-value-system
```
Verify `cl_waiting = 0` in `SHOW POOLS;`. **Rollback**: Revert `pgbouncer.defaultPoolSize=20`.

---

## 4. Prevention
- [ ] Enforce `DATABASE_STATEMENT_CACHE_SIZE=0` for PgBouncer `pool_mode = transaction`
- [ ] Set PostgreSQL `idle_in_transaction_session_timeout = '60s'`

---

## 5. Escalation
Escalate to DBA On-Call after 15 minutes with `SHOW POOLS;` and `pg_stat_activity` output.

---

## 6. References
- [HA Architecture](../operations/ha-architecture.md)
- [Slow Queries Runbook](incident-slow-queries.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Verify `PostgresDatabase.get_pool_stats()` and `pgbouncer.yaml` via `pytest tests/resilience/test_load_distribution.py -v`.

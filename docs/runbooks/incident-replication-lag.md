# Runbook: PostgreSQL Replication Lag

**Severity**: P2 (Warning) / P1 (Critical if lag `> 100MB` or exceeds RPO)  
**Alert**: `PostgresReplicationLagHigh`, `PostgresReplicationBroken`  
**SLO Impact**: RPO `< 15m`, HA Failover Readiness (`99.9%` availability)  
**Estimated MTTR**: 15–30 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Database Team

---

## 1. Symptoms
- [ ] `patronictl list` reports standby `Lag in MB > 1` (or `> 100` for sustained periods)
- [ ] Read replicas serve stale state or fail health checks
- [ ] WAL archive backlog grows on the primary node (`pg_wal` disk usage rising)
- [ ] Automated failover risk increases because standby cannot be promoted without data loss

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect Patroni cluster replication status and lag
kubectl exec -n product-value-system postgresql-ha-0 -- patronictl list

# 2. Check pg_stat_replication on the primary
kubectl exec -n product-value-system postgresql-ha-0 -- \
  psql -U pvs -d pvs -c \
  "SELECT client_addr, state, sync_state,
          pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS replay_lag_bytes,
          write_lag, flush_lag, replay_lag
   FROM pg_stat_replication;"

# 3. Check primary & standby disk/CPU saturation
kubectl top pods -n product-value-system -l app.kubernetes.io/component=database
```

### Decision Tree
- Bulk write storm or long-running analytical query blocking WAL replay -> Section 3.A
- Standby PVC I/O or disk nearly full -> [Disk Full](incident-disk-full.md)
- Standby diverged or WAL segment recycled before transfer (`requested WAL segment has already been removed`) -> Section 3.B
- Primary unreachable or split-brain -> [Database Primary Down](incident-database-primary-down.md)

---

## 3. Remediation

### 3.A: Relieve Replay Contention on Standby
```bash
# Check long-running read queries on the lagging standby (e.g., postgresql-ha-1)
kubectl exec -n product-value-system postgresql-ha-1 -- \
  psql -U pvs -d pvs -c \
  "SELECT pid, now() - query_start AS duration, state, left(query, 80)
   FROM pg_stat_activity WHERE state <> 'idle' ORDER BY duration DESC LIMIT 10;"

# Terminate blocking long-running read query if needed (replace <PID>)
kubectl exec -n product-value-system postgresql-ha-1 -- \
  psql -U pvs -d pvs -c "SELECT pg_terminate_backend(<PID>);"
```
Verify `replay_lag_bytes` trends down to `0` within 5 minutes via `patronictl list`. **Rollback**: N/A (read clients reconnect automatically via PgBouncer).

### 3.B: Reinitialize Diverged Standby via Patroni
```bash
# Re-sync a single lagging standby without affecting the primary
kubectl exec -n product-value-system postgresql-ha-0 -- \
  patronictl reinit pvs-postgres postgresql-ha-1 --force

# Monitor catch-up until State = streaming and Lag in MB = 0
kubectl exec -n product-value-system postgresql-ha-0 -- patronictl list
```
Verify `State = streaming` and `Lag in MB = 0`. **Rollback**: If reinit stalls, verify `pgbackrest` WAL archive reachability via [Backup Failure](incident-backup-failure.md).

---

## 4. Prevention
- [ ] Keep `wal_keep_size` and `pgbackrest` archive retention sized for peak write throughput (`deploy/postgres/postgresql-wal.conf`)
- [ ] Configure `hot_standby_feedback = on` or cap standby query duration to avoid WAL replay conflicts
- [ ] Monitor `pg_stat_replication` lag bytes in Prometheus and alert before `max_lag_on_failover` is reached

---

## 5. Escalation
If replication lag exceeds `100MB` for `> 15 minutes` or both standbys lag simultaneously, escalate to Database Team Lead and SRE Lead via PagerDuty. Attach `patronictl list` output and primary `pg_stat_replication` snapshot.

---

## 6. References
- [High Availability Architecture](../operations/ha-architecture.md)
- [Failover Procedures](../operations/failover-procedures.md)
- [Database Primary Down](incident-database-primary-down.md)
- [Disaster Recovery Plan](../operations/disaster-recovery-plan.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run a sustained write workload against staging `postgresql-ha-0` while throttling `postgresql-ha-1` network bandwidth, verify lag detection via `patronictl list`, and confirm catch-up to `0 MB` lag after removing throttle.

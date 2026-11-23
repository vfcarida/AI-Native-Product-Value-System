# Runbook: PostgreSQL Primary Database Down

**Severity**: P0 (Critical — Immediate Page)  
**Alert**: `SLOAvailabilityCritical` / Patroni Leader Election Failure  
**SLO Impact**: SLO-01 Availability & Durability  
**Estimated MTTR**: `< 30 seconds` (automatic) / `15 minutes` (manual intervention)  
**Last Updated**: 2026-11-23  
**Owner**: Database / SRE Team

---

## 1. Symptoms
- [ ] `/health/ready` returns HTTP 503 with `checks.database.status = error`
- [ ] `postgres-ha` leader pod crashed, unschedulable, or unreachable
- [ ] Write queries failing with connection refused or `read-only transaction`

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect Patroni cluster leadership & standby lag
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- patronictl list || \
kubectl exec -n product-value-system product-value-system-postgres-ha-1 -- patronictl list

# 2. Inspect pod status and etcd health
kubectl get pods -n product-value-system -l app=postgres-ha -o wide
```

### Decision Tree
- New Leader elected automatically within 30s -> Verify `/health/ready` recovers (no manual action needed)
- No Leader elected (split-brain / etcd unhealthy) -> Section 3.A
- All replicas corrupted or storage lost -> [Emergency Data Loss](emergency-data-loss.md)

---

## 3. Remediation

### 3.A: Manual Patroni Failover to Clean Standby
```bash
kubectl exec -n product-value-system product-value-system-postgres-ha-1 -- \
  patronictl failover product-value-system-pg-cluster --force
```
Verify a single `Leader` (`running`) appears in `patronictl list` and `/health/ready` returns `200 OK`.
**Rollback**: Old primary rejoins automatically via `use_pg_rewind: true`.

---

## 4. Prevention
- [ ] Keep 3 PostgreSQL replicas spread across separate nodes/AZs (`postgresql-ha.yaml`)
- [ ] Execute quarterly failover drill (`bash tests/resilience/test_failover.sh`)

---

## 5. Escalation
Page DBA On-Call and Engineering Manager immediately if no leader is elected within 2 minutes.

---

## 6. References
- [Failover Procedures](../operations/failover-procedures.md)
- [Emergency Data Loss](emergency-data-loss.md)
- [Disaster Recovery Plan](../operations/disaster-recovery-plan.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `bash tests/resilience/test_failover.sh` in staging verifying `< 30s` automatic promotion.

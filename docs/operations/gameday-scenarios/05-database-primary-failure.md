# GameDay Scenario: Database Primary Failure (Patroni Failover)

**Scenario ID**: `GAMEDAY-011`  
**Category**: Database Resilience  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: PROD-011 Patroni PostgreSQL HA (1 primary + 2 standbys) + PgBouncer in staging

---

## Objective
Validate automatic Patroni leader election, zero committed transaction loss (`RPO = 0`), and automatic PgBouncer reconnection within `< 30 seconds`.

**Success Criteria**:
- Patroni promotes a healthy standby within `< 30 seconds`
- Zero committed writes lost (`canary` write sequence intact)
- Application pods reconnect automatically without manual pod restart
- Former primary rejoins cluster cleanly as streaming standby

---

## Pre-GameDay Checklist
- [ ] Verify Patroni cluster healthy (`patronictl list`: 1 Leader + 2 streaming Replicas, `0 MB` lag)
- [ ] Review [Database Primary Down Runbook](../../runbooks/incident-database-primary-down.md)

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl exec -n product-value-system-staging postgresql-ha-0 -- patronictl list
```

### Phase 2: Chaos Injection (2 min)
```bash
# Run automated failover drill script against staging
NAMESPACE=product-value-system-staging bash tests/resilience/test_failover.sh
```

### Phase 3: Observation (20 min)
Measure exact wall-clock failover duration, watch `patronictl list` election, observe PgBouncer error transient, and verify Practice IC captures UTC timeline cleanly.

### Phase 4: Recovery (13 min)
Verify old primary automatically rejoins as `Replica` with `Lag in MB = 0` and all API SLOs green.

### Phase 5: Retrospective (20 min)
Review failover duration vs `< 30s` target and verify runbook commands matched live Patroni output.

---

## Cleanup
```bash
kubectl exec -n product-value-system-staging postgresql-ha-0 -- patronictl list
```

---

## Expected Outcomes
- **Green**: Automatic promotion `< 30s`, `RPO = 0`, automatic app reconnection.
- **Yellow**: Failover `30s–90s` or manual PgBouncer reconnect needed.
- **Red**: No leader elected, split-brain, or committed data lost.

## Failure Mode Analysis
Review Patroni `ttl`, `loop_wait`, `retry_timeout`, and PgBouncer service selector (`role=primary`).

## Related Scenarios
- [GAMEDAY-022: Network Partition](09-network-partition.md) · [DB Primary Down Runbook](../../runbooks/incident-database-primary-down.md)

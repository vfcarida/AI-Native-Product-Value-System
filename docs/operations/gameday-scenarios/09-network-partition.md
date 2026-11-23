# GameDay Scenario: Network Partition (Split-Brain Guard)

**Scenario ID**: `GAMEDAY-022`  
**Category**: Network Resilience  
**Difficulty**: Hard  
**Duration**: 90 minutes  
**Prerequisites**: Chaos Mesh `NetworkChaos`, Patroni PostgreSQL HA in staging

---

## Objective
Verify that partitioning an application pod or database node never creates PostgreSQL split-brain (two writable primaries), triggers fast readiness failure (`503` on `/health/ready`) to drain traffic from isolated pods, and heals automatically once the partition clears.

**Success Criteria**:
- Strictly `<= 1` writable PostgreSQL leader exists throughout the partition
- Partitioned app pod fails `/health/ready` within `15s` so Kubernetes stops routing traffic to it
- Partition heal restores streaming replication (`Lag in MB = 0`) without manual data reconciliation

---

## Pre-GameDay Checklist
- [ ] Verify Patroni cluster healthy (`patronictl list`)
- [ ] Preview manifest: `bash scripts/gameday/partition-network.sh --dry-run`

---

## Procedure

### Phase 1: Baseline (10 min)
Verify all pods `Ready` and Patroni DCS leases healthy.

### Phase 2: Chaos Injection (5 min)
```bash
bash scripts/gameday/partition-network.sh \
  --namespace product-value-system-staging \
  --target-component database \
  --direction both \
  --duration 2m
```

### Phase 3: Observation (30 min)
Observe Kubernetes endpoint removal on the partitioned app pod, verify healthy peers serve requests cleanly, and confirm `patronictl list` maintains single leader.

### Phase 4: Recovery (20 min)
```bash
bash scripts/gameday/partition-network.sh --namespace product-value-system-staging --cleanup
```
Verify partitioned pod passes `/health/ready` again and zero stale DB connections remain.

### Phase 5: Retrospective (25 min)
Document endpoint drain timing, connection pool reconnect latency, and alert accuracy.

---

## Cleanup
```bash
bash scripts/gameday/partition-network.sh --namespace product-value-system-staging --cleanup
```

---

## Expected Outcomes
- **Green**: Zero split-brain, isolated pod drained `< 15s`, clean automatic heal.
- **Yellow**: Isolated pod required manual restart to clear stale sockets post-heal.
- **Red**: Dual primary writes, data divergence, or prolonged outage across healthy pods.

## Failure Mode Analysis
Review readiness probe timeout/failureThreshold and asyncpg connection pool `command_timeout` / keepalive settings.

## Related Scenarios
- [GAMEDAY-011: Database Primary Failure](05-database-primary-failure.md) · [Replication Lag Runbook](../../runbooks/incident-replication-lag.md)

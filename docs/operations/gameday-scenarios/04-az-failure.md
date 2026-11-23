# GameDay Scenario: Availability Zone (AZ) Failure

**Scenario ID**: `GAMEDAY-004`  
**Category**: High Availability  
**Difficulty**: Hard  
**Duration**: 90 minutes  
**Prerequisites**: Multi-AZ staging cluster (`topology.kubernetes.io/zone`), Patroni HA across 3 AZs

---

## Objective
Validate that complete loss of one Availability Zone preserves API availability (`99.9%`) and PostgreSQL write continuity across the remaining two zones.

**Success Criteria**:
- Application pods and PostgreSQL standbys remain operational in `>= 2` surviving AZs
- Patroni automatically promotes a surviving-AZ standby if primary was in the failed AZ (`< 30s`)
- API error rate stays `< 2%` during failover and returns `< 1%` within 3 minutes

---

## Pre-GameDay Checklist
- [ ] Map pod and database distribution across AZs (`topology.kubernetes.io/zone`)
- [ ] Assign Practice IC to run full P1 incident workflow

---

## Procedure

### Phase 1: Baseline (10 min)
```bash
kubectl get nodes -L topology.kubernetes.io/zone
kubectl get pods -n product-value-system-staging -o wide
```

### Phase 2: Chaos Injection (15 min)
Simulate single-AZ isolation in staging by cordoning/tainting worker nodes in one zone or applying Chaos Mesh `NetworkChaos` partition targeting pods scheduled in that zone.

### Phase 3: Observation (30 min)
Monitor Patroni quorum (`patronictl list`), PgBouncer failover reconnects, HPA scale-out in surviving zones, and Grafana SLO dashboard.

### Phase 4: Recovery (15 min)
Remove zone taint/partition, verify pods rebalance across all 3 AZs, and confirm PostgreSQL streaming replication catches up (`Lag in MB = 0`).

### Phase 5: Retrospective (20 min)
Document failover timing, cross-AZ capacity headroom, and any topology spread gaps on the Kaizen board.

---

## Cleanup
```bash
bash scripts/gameday/partition-network.sh --namespace product-value-system-staging --cleanup
# Uncordon any cordoned staging AZ nodes
```

---

## Expected Outcomes
- **Green**: Surviving 2 AZs absorb 100% traffic; DB failover `< 30s` with zero data loss.
- **Yellow**: Temporary `2%–5%` errors during failover; full recovery `< 5 min`.
- **Red**: Quorum loss, split-brain, or insufficient compute capacity in remaining AZs.

## Failure Mode Analysis
Inspect `topologySpreadConstraints` and Patroni DCS quorum placement across odd number of zones (`3`).

## Related Scenarios
- [GAMEDAY-011: Database Primary Failure](05-database-primary-failure.md) · [Failover Procedures](../failover-procedures.md)

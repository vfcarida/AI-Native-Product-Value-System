# GameDay Scenario: Multiple Pod Failures (PDB Enforcement)

**Scenario ID**: `GAMEDAY-002`  
**Category**: High Availability  
**Difficulty**: Medium  
**Duration**: 45 minutes  
**Prerequisites**: PROD-011 (HPA `minReplicas: 3`, PDB `minAvailable: 2`)

---

## Objective
Verify that `PodDisruptionBudget` (`minAvailable: 2`) prevents simultaneous voluntary evictions and that HPA/Deployment recovers quickly when 2 pods experience abrupt failure.

**Success Criteria**:
- Voluntary eviction requests stop once 1 pod is unavailable (`minAvailable: 2` enforced)
- Remaining replica serves staging traffic without total outage
- Deployment restores `3/3` ready pods within `90s`

---

## Pre-GameDay Checklist
- [ ] Verify PDB exists: `kubectl get pdb -n product-value-system-staging`
- [ ] Start continuous health probe loop (`smoke-tests.sh` every 15s)

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl describe pdb -n product-value-system-staging
```

### Phase 2: Chaos Injection (5 min)
```bash
bash scripts/gameday/kill-random-pod.sh --namespace product-value-system-staging --mode fixed-percent --dry-run
# Trigger voluntary eviction test first to confirm PDB blocks excess evictions
kubectl get pods -n product-value-system-staging -l app.kubernetes.io/name=product-value-system
```

### Phase 3: Observation (15 min)
Monitor `ALLOWED DISRUPTIONS` on `kubectl get pdb -w`, API p99 latency, and `SLOAvailabilityBudgetBurn` alert state.

### Phase 4: Recovery (5 min)
```bash
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=product-value-system \
  -n product-value-system-staging --timeout=180s
```

### Phase 5: Retrospective (15 min)
Record whether PDB blocked unsafe evictions and whether HPA scaled smoothly under remaining pod load.

---

## Cleanup
```bash
bash scripts/gameday/kill-random-pod.sh --namespace product-value-system-staging --cleanup
```

---

## Expected Outcomes
- **Green**: PDB enforces `minAvailable: 2`, zero total outage, full recovery `< 90s`.
- **Yellow**: Temporary latency elevation (`2s–5s`) on remaining pods during scale recovery.
- **Red**: All pods evicted simultaneously or service completely unavailable (`503`).

## Failure Mode Analysis
Review PDB label selectors against Deployment pod labels if `ALLOWED DISRUPTIONS` did not decrement.

## Related Scenarios
- [GAMEDAY-001: Single Pod Failure](01-pod-failure.md) · [GAMEDAY-003: Node Failure](03-node-failure.md)

# GameDay Scenario: Worker Node Failure

**Scenario ID**: `GAMEDAY-003`  
**Category**: High Availability  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: Multi-node staging cluster with pod anti-affinity enabled

---

## Objective
Confirm that losing an entire Kubernetes worker node causes zero downtime because pod anti-affinity spreads replicas across distinct nodes and Kubernetes reschedules evicted workloads cleanly.

**Success Criteria**:
- Replicas are spread across `>= 3` distinct nodes before drain
- Draining one node leaves `>= 2` healthy replicas serving traffic continuously
- Evicted pod reschedules and passes `/health/ready` within `120s`

---

## Pre-GameDay Checklist
- [ ] Verify staging cluster has `>= 3` schedulable worker nodes
- [ ] Confirm pod node placement: `kubectl get pods -n product-value-system-staging -o wide`

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl get nodes
kubectl get pods -n product-value-system-staging -o wide
```

### Phase 2: Chaos Injection (10 min)
```bash
TARGET_NODE="$(kubectl get pods -n product-value-system-staging -l app.kubernetes.io/name=product-value-system -o jsonpath='{.items[0].spec.nodeName}')"
kubectl drain "${TARGET_NODE}" --ignore-daemonsets --delete-emptydir-data --timeout=3m
```

### Phase 3: Observation (20 min)
Verify PDB respected during drain, remaining pods absorb load cleanly, and evicted pod schedules onto a healthy node.

### Phase 4: Recovery (10 min)
```bash
kubectl uncordon "${TARGET_NODE}"
kubectl get pods -n product-value-system-staging -o wide
```

### Phase 5: Retrospective (15 min)
Review rescheduling duration, cluster autoscaler responsiveness, and anti-affinity rules.

---

## Cleanup
```bash
kubectl uncordon "${TARGET_NODE}"
```

---

## Expected Outcomes
- **Green**: Zero `503` responses; evicted pod rescheduled `< 120s`.
- **Yellow**: Pending pod waited `> 120s` for node capacity.
- **Red**: Multiple replicas co-located on drained node caused service outage.

## Failure Mode Analysis
Audit `podAntiAffinity` (`kubernetes.io/hostname`) in `deploy/helm/product-value-system/templates/deployment.yaml`.

## Related Scenarios
- [GAMEDAY-004: Availability Zone Failure](04-az-failure.md) · [HA Architecture](../ha-architecture.md)

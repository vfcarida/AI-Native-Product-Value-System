# GameDay Scenario: Memory Exhaustion & OOM Handling

**Scenario ID**: `GAMEDAY-031`  
**Category**: Resource Exhaustion  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: Chaos Mesh `StressChaos`, `PodMemoryHigh` alert (`> 85%`), HPA memory scaling

---

## Objective
Validate that memory pressure on a single pod triggers `PodMemoryHigh` warning before OOMKill, that HPA scales out if cluster-wide, and that an OOMKilled pod (`Exit Code 137`) restarts cleanly without cascading impact to peer replicas.

**Success Criteria**:
- `PodMemoryHigh` fires when container RSS exceeds `85%` of limit
- Peer replicas remain unaffected (strict cgroup memory limit isolation)
- Terminated pod recovers to `Ready` within `60s` after stress clears

---

## Pre-GameDay Checklist
- [ ] Check baseline memory usage: `kubectl top pods -n product-value-system-staging`
- [ ] Preview chaos manifest: `bash scripts/gameday/exhaust-memory.sh --dry-run`

---

## Procedure

### Phase 1: Baseline (5 min)
Record pod memory requests, limits, and current RSS.

### Phase 2: Chaos Injection (5 min)
```bash
bash scripts/gameday/exhaust-memory.sh \
  --namespace product-value-system-staging \
  --workers 2 --size 512MB --duration 3m
```

### Phase 3: Observation (20 min)
Watch `kubectl top pods`, Grafana container memory panel, `PodMemoryHigh` alert, and Practice IC execution of [Pod OOM Kill Runbook](../../runbooks/incident-oom-kill.md).

### Phase 4: Recovery (10 min)
```bash
bash scripts/gameday/exhaust-memory.sh --namespace product-value-system-staging --cleanup
```
Verify all replicas healthy and memory returns to baseline.

### Phase 5: Retrospective (20 min)
Evaluate whether `PodMemoryHigh` evaluation window gave sufficient lead time ahead of OOMKill.

---

## Cleanup
```bash
bash scripts/gameday/exhaust-memory.sh --namespace product-value-system-staging --cleanup
```

---

## Expected Outcomes
- **Green**: Isolated to 1 pod, alert fires, automatic restart `< 60s`, overall API `5xx < 1%`.
- **Yellow**: OOMKill happened before 5m `PodMemoryHigh` alert fired.
- **Red**: Node memory pressure evicted healthy peer pods.

## Failure Mode Analysis
Compare `resources.requests.memory` vs `resources.limits.memory` (QoS Guaranteed/Burstable class) and L1 cache size bounds.

## Related Scenarios
- [GAMEDAY-032: CPU Saturation](12-cpu-saturation.md) · [Pod OOM Kill Runbook](../../runbooks/incident-oom-kill.md)

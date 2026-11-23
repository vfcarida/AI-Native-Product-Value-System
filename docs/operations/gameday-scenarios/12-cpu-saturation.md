# GameDay Scenario: CPU Saturation & HPA Scale-Out

**Scenario ID**: `GAMEDAY-032`  
**Category**: Resource Exhaustion  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: HPA (`minReplicas: 3`, `maxReplicas: 10`, target CPU `70%`), `PodCPUThrottled` alert

---

## Objective
Validate that sustained CPU load triggers `HorizontalPodAutoscaler` scale-out smoothly, that `PodCPUThrottled` alerts accurately, and that `scripts/runbook-automation/auto-scale-up.sh` relieves latency immediately during sharp spikes.

**Success Criteria**:
- HPA scales replicas upward (`3 -> 5+`) within `90 seconds` of crossing `70%` CPU
- API p99 latency stabilizes `< 1s` once new replicas join endpoints
- HPA scales down safely after load stops (respecting 5-minute stabilization window)

---

## Pre-GameDay Checklist
- [ ] Verify metrics-server and HPA active: `kubectl get hpa -n product-value-system-staging`
- [ ] Review [High CPU Usage Runbook](../../runbooks/incident-high-cpu-usage.md)

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl get hpa -n product-value-system-staging
kubectl top pods -n product-value-system-staging
```

### Phase 2: Chaos Injection (10 min)
Run staging spike workload (`k6 run tests/load/k6-spike-test.js`) or Chaos Mesh CPU `StressChaos` (`workers: 2`, `load: 85`, `duration: 5m`) in `product-value-system-staging`.

### Phase 3: Observation (20 min)
Watch `kubectl get hpa -n product-value-system-staging -w`, `PodCPUThrottled`, and API p99 latency. Practice running `auto-scale-up.sh --dry-run` / live if spike outpaces HPA polling.

### Phase 4: Recovery (10 min)
Stop stress workload and verify HPA scales back smoothly without flapping.

### Phase 5: Retrospective (15 min)
Tune HPA `scaleUp` / `scaleDown` stabilization policies if throttling persisted `> 2 min`.

---

## Cleanup
```bash
kubectl delete stresschaos pvs-gameday-cpu-stress -n product-value-system-staging --ignore-not-found
```

---

## Expected Outcomes
- **Green**: HPA scales `< 90s`, p99 stays `< 2s`, zero `5xx` errors.
- **Yellow**: Brief CPU throttling before scale-up completes.
- **Red**: HPA failed to scale (missing metrics-server or resource requests) causing sustained outage.

## Failure Mode Analysis
Check `kubectl describe hpa` events, container CPU `requests` vs `limits`, and metrics-server health.

## Related Scenarios
- [GAMEDAY-031: Memory Exhaustion](11-memory-exhaustion.md) · [High CPU Usage Runbook](../../runbooks/incident-high-cpu-usage.md)

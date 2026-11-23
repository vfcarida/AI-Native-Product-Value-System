# GameDay Scenario: Single Pod Failure

**Scenario ID**: `GAMEDAY-001`  
**Category**: High Availability  
**Difficulty**: Easy  
**Duration**: 30 minutes  
**Prerequisites**: PROD-011 (3+ replicas & PodDisruptionBudget deployed in staging)

---

## Objective
Validate that the application handles sudden termination of a single pod with zero user-visible `503` errors and automatic Kubernetes recovery.

**Success Criteria**:
- Error rate remains `< 1%` throughout failure and recovery
- API p99 latency stays `< 2s`
- Replacement pod reaches `Ready` within `60s`
- Zero dropped requests during termination draining

---

## Pre-GameDay Checklist
- [ ] Confirm staging namespace `product-value-system-staging` has `3/3` ready pods
- [ ] Open Grafana `pvs-slo-dashboard` and announce start in `#gameday`
- [ ] Assign Facilitator, Chaos Injector, Practice IC, and Observer

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl get deployment product-value-system -n product-value-system-staging -o jsonpath='{.status.readyReplicas}{"\n"}'
k6 run tests/load/k6-greenfield.js --vus 10 --duration 5m &
```

### Phase 2: Chaos Injection (2 min)
```bash
bash scripts/gameday/kill-random-pod.sh --namespace product-value-system-staging --dry-run
bash scripts/gameday/kill-random-pod.sh --namespace product-value-system-staging --mode one --duration 30s
```

### Phase 3: Observation (10 min)
Watch `kubectl get pods -n product-value-system-staging -w` and Grafana SLO panels. Record peak error rate, p99 latency, and time until replacement pod becomes `1/1 Ready`.

### Phase 4: Recovery (3 min)
```bash
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=product-value-system \
  -n product-value-system-staging --timeout=120s
```

### Phase 5: Retrospective (10 min)
Compare observations against criteria, document findings in a `gameday` GitHub issue, and add any startup-latency improvements to the Kaizen board.

---

## Cleanup
```bash
bash scripts/gameday/kill-random-pod.sh --namespace product-value-system-staging --cleanup
```

---

## Expected Outcomes
- **Green**: Error rate `< 1%`, p99 `< 2s`, replacement pod ready `< 60s`.
- **Yellow**: Brief `1%–2%` error spike or startup `60–120s`.
- **Red**: Sustained `503` errors `> 2%` or pod fails to restart.

## Failure Mode Analysis
Inspect graceful shutdown (`preStop` sleep & `terminationGracePeriodSeconds`), readiness probe timing, and image pull policy if errors occurred.

## Related Scenarios
- [GAMEDAY-002: Multiple Pod Failures](02-multiple-pod-failures.md) · [Pod CrashLoop Runbook](../../runbooks/incident-pod-crashloop.md)

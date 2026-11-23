# Runbook: Pod CrashLoopBackOff

**Severity**: P1 (Critical if `< 2` replicas healthy) / P2 (Warning for single pod)  
**Alert**: `SLOAvailabilityCritical`, `KubePodCrashLooping`  
**SLO Impact**: SLO-01 Availability (`99.9%`), HA Pod Redundancy (`minAvailable: 2`)  
**Estimated MTTR**: 10–15 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team

---

## 1. Symptoms
- [ ] `kubectl get pods -n product-value-system` shows `CrashLoopBackOff` or high `RESTARTS` count
- [ ] Startup probe (`/health/startup`) or liveness probe (`/health/live`) fails repeatedly
- [ ] Rolling deployment stalls because new pods never reach `Ready` state
- [ ] `SLOAvailabilityBudgetBurn` or `SLOAvailabilityCritical` fires if healthy replicas drop below capacity

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Identify crashing pods and restart counts
kubectl get pods -n product-value-system -o wide

# 2. Inspect termination reason and events (Exit Code 137 = OOM, 1 = app error)
kubectl describe pod -n product-value-system <CRASHING_POD> | tail -n 40

# 3. Read logs from the previous crashed container instance
kubectl logs -n product-value-system <CRASHING_POD> --previous --tail=100
```

### Decision Tree
- `Last State: Terminated / Reason: OOMKilled (Exit Code: 137)` -> [OOM Kill](incident-oom-kill.md)
- Crash started immediately after a Helm release or config change -> Section 3.A
- Missing secret or Vault authentication error in startup logs -> [Vault Sealed](incident-vault-sealed.md)
- Transient node/pod corruption with isolated pod failure -> Section 3.B

---

## 3. Remediation

### 3.A: Roll Back Bad Release or Config
```bash
# Inspect recent Helm revisions
helm history product-value-system -n product-value-system

# Roll back immediately to last known healthy revision
helm rollback product-value-system -n product-value-system --wait --timeout 5m
```
Follow [Rollback Procedure](rollback-procedure.md) for post-rollback smoke verification. **Rollback**: Re-deploy fixed revision once root cause is validated with `python -W error -m product_value_system doctor --repo-root .`.

### 3.B: Recycle Isolated Unhealthy Pods
```bash
# Dry-run first to confirm affected pods
./scripts/runbook-automation/restart-unhealthy-pods.sh --dry-run

# Delete crashing pods so Deployment recreates healthy replacements
./scripts/runbook-automation/restart-unhealthy-pods.sh
```
Verify all replicas return to `1/1 Running` and `/health/ready` returns `200 OK`. **Rollback**: N/A (Kubernetes Deployment controller maintains replica count).

---

## 4. Prevention
- [ ] Run `python -W error -m product_value_system doctor --repo-root .` in CI before building images
- [ ] Validate Helm templates with `helm template` and `pytest tests/unit/test_helm_chart.py` prior to release
- [ ] Enforce canary/rolling update `maxUnavailable: 0` with `/health/startup` gate so faulty images halt rollout

---

## 5. Escalation
If pods continue crashing after rollback or healthy replicas drop to `< 2`, declare a P1 incident and escalate to Engineering Lead + SRE Lead via PagerDuty. Share `kubectl logs --previous` and `helm history` in `#incidents`.

---

## 6. References
- [Rollback Procedure](rollback-procedure.md)
- [Deployment Checklist](deployment-checklist.md)
- [OOM Kill](incident-oom-kill.md)
- [API Unavailable](incident-api-unavailable.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Inject an invalid environment variable in staging, confirm startup probe blocks rollout while existing replicas stay healthy, and verify `helm rollback` + `restart-unhealthy-pods.sh --dry-run` restore full readiness.

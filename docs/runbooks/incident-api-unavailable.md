# Runbook: API Service Unavailable (Availability SLO Breach)

**Severity**: P0 (Critical — Immediate Page)  
**Alert**: `SLOAvailabilityCritical`, `SLOAvailabilityMultiWindowBurn`, `SLOAvailabilityBudgetBurn`  
**SLO Impact**: SLO-01 Availability (`99.9%` over 28d)  
**Estimated MTTR**: 10–15 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team

---

## 1. Symptoms
- [ ] `SLOAvailabilityCritical` (10x burn) or `SLOAvailabilityMultiWindowBurn` (14.4x burn) pages PagerDuty
- [ ] `/health/ready` or external ingress returns HTTP 502/503/504 or connection refused
- [ ] Fewer than 2 pods in `Ready` state

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
kubectl get deployment,pods,pdb,hpa,svc,ingress -n product-value-system
kubectl describe pods -n product-value-system -l app.kubernetes.io/name=product-value-system | tail -n 60
kubectl get events -n product-value-system --sort-by='.lastTimestamp' | tail -n 25
```

### Decision Tree
- `0` running pods or `CrashLoopBackOff` -> [Pod CrashLoop](incident-pod-crashloop.md) or [Rollback Procedure](rollback-procedure.md)
- Pods running but `0/1 Ready` -> Inspect `/health/ready` dependency check (`database`, `sandbox`, `llm`)
- Cluster node outage -> Verify pod rescheduling and PDB headroom

---

## 3. Remediation

### 3.A: Restore Healthy Replicas Immediately
1. If a recent release triggered the outage, run [Rollback Procedure](rollback-procedure.md).
2. Otherwise scale healthy replicas and recycle stuck pods:
   ```bash
   ./scripts/runbook-automation/auto-scale-up.sh --increment 2
   ./scripts/runbook-automation/restart-unhealthy-pods.sh
   ```
3. Verify external availability:
   ```bash
   kubectl exec -n product-value-system deploy/product-value-system -- curl -fsS http://localhost:8000/health/ready
   ```

---

## 4. Prevention
- [ ] Maintain `minReplicas: 3`, `minAvailable: 2`, and cross-zone `podAntiAffinity` ([HA Architecture](../operations/ha-architecture.md))
- [ ] Freeze feature deployments if 28-day error budget falls below `25%`

---

## 5. Escalation
Page Backup On-Call and Engineering Manager immediately if availability is not restored within 10 minutes. Post incident timeline and current ready-pod count in `#incidents`.

---

## 6. References
- [HA Architecture](../operations/ha-architecture.md)
- [Failover Procedures](../operations/failover-procedures.md)
- [Rollback Procedure](rollback-procedure.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `bash tests/resilience/test_zero_downtime.sh` and confirm 100% HTTP 200 continuity.

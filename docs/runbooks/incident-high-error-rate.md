# Runbook: High HTTP 5xx Error Rate

**Severity**: P2 (`> 1%`) / P1 (`> 5%`)  
**Alert**: `SLOErrorRateHigh`, `SLOErrorRateCritical`  
**SLO Impact**: SLO-03 Error Rate (`< 1%`)  
**Estimated MTTR**: 10–15 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Platform Engineering

---

## 1. Symptoms
- [ ] `SLOErrorRateHigh` or `SLOErrorRateCritical` alert fires
- [ ] Elevated HTTP 500/502/503/504 responses in `/metrics` and Jaeger error spans
- [ ] Users report failed API requests

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Check recent deployments (most common cause of sudden 5xx)
helm history product-value-system -n product-value-system --max 5

# 2. Tail ERROR/CRITICAL logs across pods
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --since=10m | grep -iE "error|traceback|critical" | tail -n 50

# 3. Verify readiness & dependency checks
kubectl exec -n product-value-system deploy/product-value-system -- curl -s http://localhost:8000/health/ready
```

### Decision Tree
- Error spike started within 30 minutes of a deploy -> Section 3.A (Rollback)
- Readiness reports `database: error` -> [Database Primary Down](incident-database-primary-down.md)
- Logs show upstream LLM 5xx -> [LLM Provider Outage](incident-llm-provider-outage.md)

---

## 3. Remediation

### 3.A: Immediate Deployment Rollback
Execute [Rollback Procedure](rollback-procedure.md):
```bash
helm rollback product-value-system 0 -n product-value-system --wait --timeout=5m
```
Verify 5xx rate returns `< 1%`. **Rollback**: Re-deploy patched release after root-cause fix.

### 3.B: Recycle Unhealthy Pods
```bash
./scripts/runbook-automation/restart-unhealthy-pods.sh
```

---

## 4. Prevention
- [ ] Add regression tests covering the failing code path in `tests/`
- [ ] Enforce progressive rollout validation ([Deployment Checklist](deployment-checklist.md))

---

## 5. Escalation
Escalate to Backup On-Call after 15 minutes if rollback does not clear 5xx errors; share error tracebacks and Jaeger `otel.status_code=ERROR` trace IDs in `#incidents`.

---

## 6. References
- [Rollback Procedure](rollback-procedure.md)
- [Troubleshooting Flowchart](troubleshooting-flowchart.md)
- [SLO Definitions](../operations/slos.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Inject HTTP 503 readiness check in staging and verify `helm rollback` restores `< 1%` error rate in `< 5 min`.

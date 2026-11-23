# Rollback Procedure

**Severity**: P1 (execute immediately on deployment regression)  
**Estimated Time**: `< 5 minutes` (application rollback)  
**Owner**: Release Engineer / On-Call SRE

---

## 1. When to Roll Back

Roll back immediately if a deployment causes error rate `> 2%`, p99 latency `> 5s`, `CrashLoopBackOff`, or failed readiness probes. Never debug live in production while customer SLOs are burning.

---

## 2. Fast Application Rollback (Helm)

```bash
# 1. Inspect Helm release history
helm history product-value-system -n product-value-system --max 10

# 2. Roll back to last known-good revision
helm rollback product-value-system 0 -n product-value-system --wait --timeout=5m

# 3. Confirm rollout completed with 0 unavailable pods
kubectl rollout status deployment/product-value-system -n product-value-system --timeout=300s
kubectl get pods -n product-value-system
```

---

## 3. Kubernetes Native Rollback (Fallback if Helm Unavailable)

```bash
kubectl rollout undo deployment/product-value-system -n product-value-system
kubectl rollout status deployment/product-value-system -n product-value-system --timeout=300s
```

---

## 4. Database Migration Rollback (Only If Schema Changed)

> Prefer backward-compatible expand/contract migrations so application rollback does not require destructive schema downgrades.

If a migration must be reversed:
```bash
# Option A: Alembic downgrade (only if no new production data depends on added columns)
alembic downgrade -1

# Option B: Point-in-Time Recovery to pre-deploy timestamp
./scripts/pitr-recovery.sh "<PRE_DEPLOY_UTC_TIMESTAMP>"
```

---

## 5. Post-Rollback Verification

```bash
kubectl exec -n product-value-system deploy/product-value-system -- curl -fsS http://localhost:8000/health/ready
```

- [ ] Confirm Prometheus 5xx error rate returns `< 1%` and p99 `< 1s`.
- [ ] Post rollback summary in `#deployments` and `#incidents`.
- [ ] File follow-up ticket before attempting forward-fix redeploy.

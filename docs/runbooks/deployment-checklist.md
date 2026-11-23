# Deployment Checklist (Zero-Downtime Production Rollout)

**Purpose**: Ensure safe, repeatable, zero-downtime deployments to production.  
**Estimated Time**: 30–45 minutes (including post-deploy monitoring)  
**Owner**: Release Engineer / On-Call SRE

---

## Pre-Deployment (15 minutes)

1. **Code & Quality Gates**
   - [ ] All PRs approved (`>= 2` reviewers) and CI green (`make lint`, `make test`, `security-scan.yml`, `performance-regression.yml`).
   - [ ] `python -W error -m product_value_system doctor --repo-root .` reports `HEALTHY`.
   - [ ] Database migrations tested idempotently in staging (`migrations/postgres/` or `alembic upgrade head`).
2. **Communication & Freeze Check**
   - [ ] Announced in `#deployments` with release tag and rollback owner.
   - [ ] Confirm no active P0/P1 incidents in `#incidents` and SLO error budget remaining `> 25%` ([SLO Dashboard](../operations/slos.md)).
3. **Backup & Rollback Preparation**
   - [ ] Latest encrypted backup verified `< 1 hour` old (`pgbackrest --stanza=product-value-system info`).
   - [ ] Record current live image tag:
     ```bash
     PREVIOUS_TAG="$(kubectl get deployment product-value-system -n product-value-system -o jsonpath='{.spec.template.spec.containers[0].image}' | cut -d: -f2)"
     echo "Previous tag: ${PREVIOUS_TAG}"
     ```
   - [ ] Review [Rollback Procedure](rollback-procedure.md).
4. **Cluster Headroom Check**
   ```bash
   kubectl top nodes
   kubectl get hpa,pdb -n product-value-system
   ```

---

## Deployment Execution (10 minutes)

```bash
NEW_VERSION="v1.x.y"

helm upgrade product-value-system deploy/helm/product-value-system \
  --namespace product-value-system \
  --set image.tag="${NEW_VERSION}" \
  --wait --timeout=10m

kubectl rollout status deployment/product-value-system -n product-value-system --timeout=300s
```

---

## Post-Deployment Validation (15 minutes)

```bash
# 1. Verify probes & pod distribution
kubectl get pods -n product-value-system -o wide
kubectl exec -n product-value-system deploy/product-value-system -- curl -fsS http://localhost:8000/health/ready
kubectl exec -n product-value-system deploy/product-value-system -- curl -fsS http://localhost:8000/health/startup

# 2. Check recent logs for errors
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --since=5m | grep -iE "error|critical|exception" || true
```

- [ ] Confirm p99 latency `< 1s` and 5xx error rate `< 1%` in Grafana.
- [ ] Confirm traces flowing normally in Jaeger ([Tracing Guide](../operations/tracing-guide.md)).
- [ ] Announce completion in `#deployments` after 15 minutes of clean telemetry.

---

## Immediate Rollback Triggers

Initiate [Rollback Procedure](rollback-procedure.md) immediately if ANY trigger fires:
- 5xx error rate `> 2%` (2x SLO budget)
- API p99 latency `> 5s`
- Any pod enters `CrashLoopBackOff` (`> 3` restarts in 10 minutes)
- Database migration failure or data integrity alert

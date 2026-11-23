# Runbook: Disk Space Full (PVC / Ephemeral Storage)

**Severity**: P1 (Critical if `>= 90%` on database PVC) / P2 (Warning at `>= 80%`)  
**Alert**: `KubePersistentVolumeFillingUp`, `NodeFilesystemAlmostOutOfSpace`  
**SLO Impact**: SLO-01 Availability (`99.9%`), Data Durability & Audit Logging  
**Estimated MTTR**: 15–30 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team / Database Team

---

## 1. Symptoms
- [ ] PersistentVolumeClaim (`postgresql-ha` data volume or audit storage) reaches `>= 85%` capacity
- [ ] PostgreSQL logs `PANIC: could not write to file "pg_wal/...": No space left on device`
- [ ] Kubernetes evicts application pods with `Reason: Evicted` (`ephemeral-storage` pressure)
- [ ] WAL archiving or local backup staging fails

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect PVC sizes and storage classes
kubectl get pvc -n product-value-system

# 2. Check disk utilization inside PostgreSQL primary pod
kubectl exec -n product-value-system postgresql-ha-0 -- df -h /var/lib/postgresql/data

# 3. Inspect largest subdirectories (pg_wal vs base vs pg_log)
kubectl exec -n product-value-system postgresql-ha-0 -- \
  du -sh /var/lib/postgresql/data/pgdata/* 2>/dev/null | sort -hr | head -n 10
```

### Decision Tree
- `pg_wal/` growing rapidly because WAL archiving is failing -> Fix archive target first via [Backup Failure](incident-backup-failure.md)
- `base/` data directory genuinely growing (audit logs / run history) -> Section 3.A + Section 3.B
- Node ephemeral storage full from unrotated container logs or sandbox images -> Section 3.C

---

## 3. Remediation

### 3.A: Expand Database PersistentVolumeClaim Online
```bash
# Verify storage class supports allowVolumeExpansion: true
kubectl get sc fast-ssd -o jsonpath='{.allowVolumeExpansion}{"\n"}'

# Patch PVC size (example: 100Gi -> 200Gi) for each StatefulSet PVC
for pvc in $(kubectl get pvc -n product-value-system -l app.kubernetes.io/component=database -o name); do
  kubectl patch -n product-value-system "$pvc" \
    -p '{"spec":{"resources":{"requests":{"storage":"200Gi"}}}}'
done
```
Verify `df -h` reflects expanded capacity and utilization drops `< 60%`. **Rollback**: Kubernetes PVCs cannot be shrunk online; right-size during a scheduled maintenance window if over-provisioned.

### 3.B: Archive Expired Audit Logs to Cold S3 Storage
```bash
RELEASE="${RELEASE:-pvs}"
JOB_NAME="manual-archive-$(date +%Y%m%d%H%M)"

# Trigger compliance retention archival job immediately (moves >90d logs to Glacier/S3)
kubectl create job -n product-value-system \
  --from="cronjob/${RELEASE}-audit-archival-job" "${JOB_NAME}"

# Monitor archival job completion
kubectl logs -n product-value-system -l "job-name=${JOB_NAME}" -f
```
Verify job succeeds and reclaimed storage is visible. **Rollback**: Archived records remain immutable in S3 (`docs/compliance/retention-policy.md`).

### 3.C: Clear Ephemeral Sandbox Artifacts on Evicted Nodes
```bash
# Recycle evicted pods cleanly
./scripts/runbook-automation/restart-unhealthy-pods.sh
```
Coordinate with Platform Ops to prune dangling Docker/containerd sandbox layers on affected worker nodes if node disk `> 85%`.

---

## 4. Prevention
- [ ] Alert at `75%` warning and `85%` critical on all database PVCs so expansion occurs before saturation
- [ ] Ensure weekly `cronjob-archive.yaml` succeeds and alerts on failure ([Retention Policy](../compliance/retention-policy.md))
- [ ] Enforce `ephemeral-storage` requests/limits on application and sandbox pods

---

## 5. Escalation
If PostgreSQL enters read-only/panic mode due to `100%` disk full or PVC expansion is blocked by cloud quota, escalate immediately to Database Team Lead and Cloud Infrastructure On-Call via PagerDuty.

---

## 6. References
- [Disaster Recovery Plan](../operations/disaster-recovery-plan.md)
- [Retention Policy](../compliance/retention-policy.md)
- [Backup Failure](incident-backup-failure.md)
- [Database Primary Down](incident-database-primary-down.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Provision a test PVC with `allowVolumeExpansion: true` in staging, expand from `10Gi` to `15Gi` while PostgreSQL serves traffic, and verify online filesystem resize with zero downtime.

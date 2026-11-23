# Runbook: Database Backup Failure or Verification Alert

**Severity**: P1 (Critical for `BackupFailed`) / P2 (Warning for `BackupOld` or `BackupSizeTooSmall`)  
**Alert**: `BackupFailed`, `BackupOld`, `BackupSizeTooSmall`  
**SLO Impact**: Data Durability (RPO `< 15m`, RTO `< 1h`), SOC2 Backup Compliance  
**Estimated MTTR**: 20–45 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Database Team / SRE Team

---

## 1. Symptoms
- [ ] `BackupFailed` fires (`backup_verification_success == 0` for 5m)
- [ ] `BackupOld` fires (`backup_age_seconds > 7200` — no successful snapshot in > 2 hours)
- [ ] `BackupSizeTooSmall` fires (latest backup `< 50%` of 7-day rolling average — possible truncation)
- [ ] Hourly `pvs-postgres-backup` or weekly `pvs-backup-verification` Kubernetes CronJob reports `Failed`

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect recent backup and verification CronJob runs
kubectl get cronjobs,jobs -n product-value-system | grep -i backup

# 2. Read logs from the latest failed backup or verification pod
kubectl logs -n product-value-system -l app.kubernetes.io/component=backup --tail=150

# 3. Inspect pgBackRest stanza health and latest backup timestamps
kubectl exec -n product-value-system postgresql-ha-0 -- pgbackrest --stanza=pvs info
```

### Decision Tree
- S3 credential error (`403 Forbidden`) or encryption key mismatch -> Section 3.A
- Disk full on primary or WAL archive queue stalled -> [Disk Full](incident-disk-full.md)
- `BackupSizeTooSmall` or checksum corruption in latest snapshot -> Section 3.B (Force fresh full backup + verification)
- Actual database corruption or accidental deletion discovered -> [Emergency Data Loss](emergency-data-loss.md)

---

## 3. Remediation

### 3.A: Repair S3 / Encryption Secret Access
```bash
# Confirm backup secret exists and contains required keys (names only — never print values)
kubectl get secret pvs-backup-credentials -n product-value-system -o jsonpath='{.data}' | tr ',' '\n' | cut -d':' -f1

# Test pgBackRest stanza check against primary (us-east-1) and DR replica (us-west-2) S3 buckets
kubectl exec -n product-value-system postgresql-ha-0 -- pgbackrest --stanza=pvs check
```
If IAM/S3 credentials expired, rotate via Vault ([Vault Integration Guide](../security/vault-integration.md)) and re-run `pgbackrest --stanza=pvs check` until exit code `0`.

### 3.B: Trigger Manual Full Backup & Restore Verification
```bash
RELEASE="${RELEASE:-pvs}"
TS="$(date +%Y%m%d%H%M)"

# 1. Trigger an immediate manual backup job from the CronJob template
kubectl create job -n product-value-system \
  --from="cronjob/${RELEASE}-postgresql-backup" "manual-backup-${TS}"

# 2. Wait for completion and verify size/integrity
kubectl wait -n product-value-system --for=condition=complete \
  "job/manual-backup-${TS}" --timeout=20m

# 3. Run the isolated restore-verification job to clear BackupFailed
kubectl create job -n product-value-system \
  --from="cronjob/${RELEASE}-backup-verification" "manual-verify-${TS}"
```
Verify `pgbackrest --stanza=pvs info` lists the new `full` backup, size aligns with 7-day baseline, and `backup_verification_success == 1`. **Rollback**: N/A (backups are append-only in S3 with Object Lock).

---

## 4. Prevention
- [ ] Maintain cross-region S3 replication (`us-east-1 -> us-west-2`) with Object Lock enabled (`deploy/postgres/pgbackrest.conf`)
- [ ] Never silence `BackupFailed` for more than 4 hours without an active remediation ticket
- [ ] Run quarterly full DR restore drills per [Disaster Recovery Plan](../operations/disaster-recovery-plan.md)

---

## 5. Escalation
If backups remain broken for `> 2 hours` (approaching compliance window) or weekly restore verification fails on two consecutive backups, escalate to Database Team Lead and SRE Lead via PagerDuty.

---

## 6. References
- [Disaster Recovery Plan](../operations/disaster-recovery-plan.md)
- [Restore Procedures](../operations/restore-procedures.md)
- [Emergency Data Loss](emergency-data-loss.md)
- [Disk Full](incident-disk-full.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `pytest tests/persistence/test_disaster_recovery.py -v` (verifies gzip + AES-256-GCM encrypted backup, checksum verification, and point-in-time restore drill) and trigger staging `backup-verification` CronJob monthly.

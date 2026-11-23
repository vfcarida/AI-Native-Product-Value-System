# Emergency Procedure: Suspected Data Loss or Corruption

**Severity**: P0 (Critical — Immediate Page & Executive Escalation)  
**Target RTO / RPO**: `< 1 hour` / `< 15 minutes`  
**Owner**: Incident Commander + Database Lead

---

## 1. Immediate Containment (First 5 Minutes)

1. **Declare P0 Incident**: Page Backup On-Call and Engineering Manager in PagerDuty; open `#incidents` war room.
2. **Stop Writes Immediately** (prevent further corruption or overwriting recoverable WAL):
   ```bash
   kubectl scale deployment product-value-system --replicas=0 -n product-value-system
   ```
3. **Preserve Forensic State**: Do NOT delete existing PostgreSQL data volumes or WAL files until a recovery snapshot is secured.

---

## 2. Identify Last Known-Good Timestamp

```bash
# Inspect backup & WAL archive availability
pgbackrest --stanza=product-value-system info

# Inspect recent audit trail entries (if audit_logs readable)
kubectl exec -n product-value-system product-value-system-postgres-ha-0 -- \
  psql -U pvs_user -d pvs_db -c "SELECT timestamp, event_type, user_id, action FROM audit_logs ORDER BY timestamp DESC LIMIT 20;"
```

---

## 3. Execute Recovery

- **Corrupted / Dropped Table**: Run Point-in-Time Recovery to 1–5 minutes prior to destructive event:
  ```bash
  ./scripts/pitr-recovery.sh "YYYY-MM-DD HH:MM:SS"
  ```
- **Region Outage / Local Storage Destroyed**: Restore from cross-region S3 `repo2` (`us-west-2`):
  ```bash
  ./scripts/restore-database.sh --repo 2 --target latest
  ```

---

## 4. Verification & Controlled Resumption

1. Verify row counts and database integrity (`audit_logs`, `value_stream_runs`, `stages`, `evaluations`, `costs`).
2. Run HMAC audit log tamper verification (`POST /api/v1/audit/verify`).
3. Restore application replicas (`kubectl scale deployment product-value-system --replicas=3 -n product-value-system`).
4. Run smoke checks (`/health/ready`, `/slos`) and record exact measured RTO/RPO.

---

## 5. References

- [Disaster Recovery Plan](../operations/disaster-recovery-plan.md)
- [Restore Procedures Runbook](../operations/restore-procedures.md)

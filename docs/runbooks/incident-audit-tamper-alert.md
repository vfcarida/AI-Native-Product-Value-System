# Runbook: Audit Log Tamper or Integrity Failure Alert

**Severity**: P1 (Critical — Compliance & Security Incident)  
**Alert**: `AuditIntegrityCheckFailed`, `AuditEmergencyFallbackActive`  
**SLO Impact**: SOC2 CC7.2 / GDPR Art. 32 / HIPAA Audit Trail Integrity  
**Estimated MTTR**: 15–30 minutes (triage & containment; full forensics ongoing)  
**Last Updated**: 2026-11-23  
**Owner**: Security Team / Compliance Team

---

## 1. Symptoms
- [ ] `POST /api/v1/audit/verify` reports `valid: false` or non-empty `tampered_ids`
- [ ] Application logs emit `AUDIT_EMERGENCY` fallback lines to stderr (database write failure for audit records)
- [ ] Unexpected `UPDATE` or `DELETE` attempt blocked by PostgreSQL immutability triggers on `audit_logs`
- [ ] Signature verification fails across a contiguous time range of audit entries

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Run cryptographic integrity verification over the last 24 hours via Audit API
kubectl exec -n product-value-system deploy/product-value-system -- \
  curl -fsS -X POST http://localhost:8000/api/v1/audit/verify \
  -H "Content-Type: application/json" \
  -d '{"limit": 5000}'

# 2. Check for AUDIT_EMERGENCY fallback entries in application logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=200 \
  | grep -E "AUDIT_EMERGENCY|tamper|signature"

# 3. Verify PostgreSQL immutability trigger is still present and enabled
kubectl exec -n product-value-system postgresql-ha-0 -- \
  psql -U pvs -d pvs -c \
  "SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = 'audit_logs'::regclass;"
```

### Decision Tree
- Verification failed **right after an HMAC signing key rotation** (older entries signed with previous key) -> Section 3.A
- `AUDIT_EMERGENCY` stderr flood because PostgreSQL is down or connection pool exhausted -> Restore DB first via [Database Primary Down](incident-database-primary-down.md) or [Connection Exhaustion](incident-database-connection-exhaustion.md), then Section 3.B
- Unexplained signature mismatch on specific rows, disabled trigger, or unauthorized DB role access -> **STOP modifying DB!** Immediately execute Section 3.C + [Emergency Security Breach](emergency-security-breach.md)

---

## 3. Remediation

### 3.A: Restore Previous Signing Key to Verification Ring (False Positive After Rotation)
Confirm key rotation audit event with Security Team. Ensure the previous signing key remains configured in `previous_signing_keys` (`config/compliance.yaml` / Vault) so historical entries verify cleanly during the retention window, then re-run `POST /api/v1/audit/verify`. **Rollback**: N/A (read-only verification config).

### 3.B: Recover Audit Pipeline & Reconcile Emergency Logs
Once PostgreSQL connectivity is restored, collect `AUDIT_EMERGENCY` JSON lines from centralized log storage (ELK/Loki) for the outage window, archive them in immutable S3 compliance storage, and document the gap in the compliance incident log.

### 3.C: Confirmed or Suspected Audit Tampering (Forensic Preservation)
1. **Do NOT run `DELETE`, `UPDATE`, or `VACUUM` on `audit_logs`** — preserve exact PostgreSQL WAL and disk state.
2. Snapshot the database PVC and copy immutable S3 archived audit segments (`docs/compliance/retention-policy.md`) for side-by-side comparison.
3. Immediately invoke [Emergency Security Breach](emergency-security-breach.md) and rotate compromised database/signing keys via [Emergency Secret Revocation](../operations/runbooks/emergency-secret-revocation.md).

---

## 4. Prevention
- [ ] Enforce PostgreSQL `BEFORE UPDATE OR DELETE` immutability triggers (`migrations/002_create_audit_tables.sql`) and restrict superuser privileges
- [ ] Run automated scheduled `POST /api/v1/audit/verify` integrity checks hourly
- [ ] Replicate signed audit archives to S3 Object Lock (`COMPLIANCE` mode) daily ([SOC2 Controls](../compliance/soc2-controls.md))

---

## 5. Escalation
Any confirmed or unexplained audit signature mismatch MUST be escalated immediately to the Security Lead, Data Protection Officer (DPO), and Legal/Compliance within **15 minutes** (GDPR 72-hour breach notification clock may apply).

---

## 6. References
- [Emergency Security Breach](emergency-security-breach.md)
- [SOC2 Controls Documentation](../compliance/soc2-controls.md)
- [GDPR Compliance Guide](../compliance/gdpr-compliance.md)
- [Retention Policy](../compliance/retention-policy.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `pytest tests/compliance/ -v` (verifies HMAC-SHA256 signing, key-rotation verification via `previous_signing_keys`, tamper detection, and emergency stderr fallback when storage raises errors).

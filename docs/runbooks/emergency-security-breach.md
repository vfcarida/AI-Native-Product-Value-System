# Emergency Procedure: Security Breach or Credential Compromise

**Severity**: P0 (Critical — Security & Legal Escalation)  
**Regulatory SLA**: GDPR Article 33 **72-hour** DPA/user notification clock starts upon breach confirmation  
**Owner**: Security Incident Commander

---

## 1. Immediate Containment (0–15 Minutes)

1. **Declare P0 Security Incident** in PagerDuty (`security@example.com` + Engineering Manager).
2. **Isolate Affected Workloads**:
   - Tighten egress `NetworkPolicy` or scale compromised deployment to `0` if active exfiltration is observed.
   - Trigger FinOps kill-switch if compromised LLM API keys are incurring spend (`EMERGENCY_STOP=true`).
3. **Revoke Compromised Credentials Immediately**:
   Execute [Emergency Secret Revocation Runbook](../operations/runbooks/emergency-secret-revocation.md) (revoke Vault leases, rotate OpenAI/Anthropic/Gemini/DB keys, rotate `AUDIT_SIGNING_KEY` while preserving prior key in `previous_signing_keys` for forensic verification).

---

## 2. Forensic Evidence Preservation (15–60 Minutes)

```bash
# 1. Verify audit trail integrity (detect tampering)
curl -fsS -X POST https://pvs.internal/api/v1/audit/verify \
  -H "X-API-Key: ${SECURITY_AUDIT_KEY}"

# 2. Export immutable SOC2/forensic CSV evidence for incident window
curl -fsS "https://pvs.internal/api/v1/audit/export?export_format=csv&start_time=<ISO_START>&end_time=<ISO_END>" \
  -H "X-API-Key: ${SECURITY_AUDIT_KEY}" -o incident_audit_evidence.csv
```

- Preserve pod logs, sandbox egress block logs, and Vault audit logs before restarting pods.
- Identify all affected `subject_id` users from exported audit evidence.

---

## 3. Eradication, Recovery & Notification

1. Redeploy clean signed container images after scanning via Trivy/Snyk (`make security-scan`).
2. Restore from immutable S3 Object Lock backup if data tampering occurred ([Emergency Data Loss](emergency-data-loss.md)).
3. Coordinate with Legal / DPO to complete GDPR Article 33 notification within **72 hours** ([GDPR Compliance](../compliance/gdpr-compliance.md)).
4. Complete blameless security postmortem and add regression tests to `tests/security/`.

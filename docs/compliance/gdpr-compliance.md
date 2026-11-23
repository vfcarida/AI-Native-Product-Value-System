# GDPR Compliance Implementation & DSAR Procedures

This guide documents technical controls implemented in `product_value_system.compliance` to support EU General Data Protection Regulation (GDPR) compliance across Articles 15, 17, 30, 32, and 33.

---

## Article 15 — Right of Access (DSAR Export)

- **Implementation**: `GDPRHandler.export_user_data()` (`src/product_value_system/compliance/gdpr_handler.py`) and `POST /api/v1/compliance/gdpr/export`.
- **SLA**: Legal maximum 30 days (`compliance.gdpr.dsar_sla_days: 30`); automated execution completes in seconds.
- **Authorization**:
  - Authenticated data subjects may export **only their own data**.
  - Compliance administrators (`compliance:admin`) may generate exports on behalf of verified data subjects.
- **Export Archive Format** (`gdpr_export_<sanitized_user_id>_<utc_timestamp>.zip`):
  - `user_data.json`: Machine-readable JSON containing:
    - `profile`: Account profile (sensitive credential fields redacted)
    - `agent_executions`: History of agent executions initiated by the user
    - `model_calls`: Model API invocation metadata with prompt/response bodies redacted (`[REDACTED_PROMPT length=...]`)
    - `audit_logs`: Compliance and security events linked to the user as subject or actor
    - `metadata`: UTC export timestamp, `user_id`, `format_version`, and `gdpr_article`
  - `README.txt`: Plain-language guide explaining each data category, user rights (rectification, erasure, objection), and DPO contact details.
  - `audit_logs.csv` (optional when `export_format="csv"`): Spreadsheet-safe CSV export.
- **Audit Events Emitted**: `gdpr.export.requested` (start) and `data.export` (completion with archive size and duration).

---

## Article 17 — Right to Erasure ("Right to Be Forgotten")

- **Implementation**: `GDPRHandler.delete_user_data()` and `POST /api/v1/compliance/gdpr/delete`.
- **SLA**: Legal maximum 30 days (`compliance.gdpr.deletion_sla_days: 30`); automated execution completes synchronously.
- **Deletion & Anonymization Scope**:
  - **User profile**: Anonymized in place (`email`, `full_name`, and `metadata` cleared; `anonymized_at` timestamp recorded) to preserve referential integrity without retaining PII.
  - **Agent executions**: Hard-deleted from operational tables.
  - **Model API call records**: Hard-deleted (aggregate non-PII Prometheus cost/token counters remain unaffected).
  - **Audit logs (Legal Exception — Article 17(3)(b))**: Retained by default (`retain_audit_logs=True`) for 7 years to satisfy SOC2, tax, and security regulatory obligations. In the exceptional case where subject anonymization inside audit logs is legally mandated, a `compliance:admin` caller may pass `retain_audit_logs=False` to replace `subject_id`/`user_id` with `[ANONYMIZED]` and strip `client_ip`/`user_agent`.
- **Audit Events Emitted**: `gdpr.deletion.requested` and `data.delete` (recording per-category deletion counts and reason).

---

## Article 30 — Records of Processing Activities

Every sensitive operation (data access, model invocation, agent run, secret access, export, and deletion) produces a structured `AuditEvent` capturing:
- Actor (`user_id`), Data Subject (`subject_id`), UTC `timestamp`
- Action description, `resource_type`, `resource_id`, and `result`
- Correlation context (`session_id`, `client_ip`, `user_agent`) and redacted `metadata`

---

## Article 32 — Security of Processing

- **Cryptographic Integrity**: RFC 2104 HMAC-SHA256 signatures computed over deterministic canonical JSON payloads (`AuditSigner`).
- **Key Management**: Signing keys loaded from `AUDIT_SIGNING_KEY`, Kubernetes `AUDIT_SIGNING_KEY_FILE` secret mounts, or HashiCorp Vault (never hardcoded in source control). Supports zero-downtime key rotation via `previous_signing_keys`.
- **Encryption at Rest**:
  - Hot database: PostgreSQL disk/volume encryption + TLS in transit.
  - Cold archive: S3 `ServerSideEncryption="AES256"` (or `aws:kms`) in `GLACIER` storage class.
- **Access Control & Least Privilege**: Append-only DB grants (`INSERT` + `SELECT` only for `pvs_user`), RBAC-protected search API, and constant-time credential comparison (`hmac.compare_digest`).

---

## Article 33 — Personal Data Breach Detection & 72-Hour Notification

1. **Detection**: Scheduled or on-demand `POST /api/v1/audit/verify` sweeps detect any altered audit record (`integrity_verified: false`, listing `tampered_event_ids`).
2. **Scoping**: Query `GET /api/v1/audit/search` with `start_time`/`end_time` around the incident window to identify all accessed `subject_id` values and actor `session_id` trails.
3. **Containment**: Revoke compromised credentials following the [Emergency Secret Revocation Runbook](../operations/runbooks/emergency-secret-revocation.md).
4. **Notification**: Export immutable evidence via `GET /api/v1/audit/export?export_format=csv` and notify the Data Protection Authority (DPA) and affected data subjects within **72 hours**.

---

## Related Documentation

- [SOC2 Trust Service Criteria Control Mapping](soc2-controls.md)
- [Audit Retention & Lifecycle Policy](retention-policy.md)

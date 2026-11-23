# Compliance Audit Log Retention & Data Lifecycle Policy

This policy defines how immutable audit trails transition across hot storage, encrypted cold archive, and end-of-life deletion in compliance with SOC2 CC7.3, GDPR storage limitation principles, and HIPAA 6+ year retention mandates.

---

## Retention Tiers

| Tier | Storage Backend | Duration | Purpose | Encryption |
|---|---|---|---|---|
| **Hot** | PostgreSQL (`audit_logs`) / SQLite WAL | **90 days** (`hot_storage_days: 90`) | Sub-second search, incident response, GDPR DSAR | TLS in transit + DB volume encryption |
| **Cold Archive** | AWS S3 (`GLACIER`) | **7 years** (`cold_storage_days: 2555`) | Regulatory retention (SOC2, HIPAA, tax/legal) | `AES256` (or `aws:kms`) + `gzip` level 9 |
| **Expiration** | Permanently deleted | **> 2,555 days** | GDPR storage limitation compliance | Deletion logged as audit event |

Configuration is strictly validated in [config/compliance.yaml](../../config/compliance.yaml) via `ComplianceConfigFile`.

---

## Automated Kubernetes CronJobs

When deployed with `--set compliance.enabled=true`, the Helm chart template [cronjob-archive.yaml](../../deploy/helm/product-value-system/templates/cronjob-archive.yaml) provisions two Restricted-PSS CronJobs:

1. **`<release>-audit-archival-job`** — Schedule: `0 2 * * *` (daily at 02:00 UTC)
   - Runs `python3 -m product_value_system.compliance.retention_policy --mode archive`
   - Queries audit rows older than `AUDIT_HOT_STORAGE_DAYS` (default `90`)
   - Batches up to 1,000 events per file, compresses with `gzip` level 9 (typically >80% reduction), uploads to:
     `s3://<bucket>/audit_logs/year=YYYY/month=MM/batch_<timestamp>_<index>.json.gz`
     with `StorageClass=GLACIER` and `ServerSideEncryption=AES256`
   - Prunes archived rows from hot storage **only after** confirmed S3 upload
2. **`<release>-audit-deletion-job`** — Schedule: `0 3 1 * *` (monthly on the 1st at 03:00 UTC)
   - Runs `python3 -m product_value_system.compliance.retention_policy --mode delete`
   - Lists `audit_logs/` objects via `list_objects_v2` paginator and removes objects whose `LastModified` timestamp exceeds `AUDIT_COLD_STORAGE_DAYS` (default `2555`)

Both CronJobs run as non-root UID `10001`, `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: ["ALL"]`, and `concurrencyPolicy: Forbid`.

---

## Dry-Run & Manual Execution

```bash
# Dry-run archival (verifies batching & compression without uploading or pruning)
python -m product_value_system.compliance.retention_policy --mode archive --dry-run

# Dry-run 7-year expiration sweep
python -m product_value_system.compliance.retention_policy --mode delete --dry-run
```

---

## Quarterly Access Reviews (SOC2 CC6.2 / CC6.3)

Run `QuarterlyAccessReviewer.generate_review_report()` every quarter to verify least-privilege access to production systems and `audit:read` endpoints:
- Dormant accounts (`>= 90 days` inactive) are flagged for revocation/recertification.
- Privileged human accounts without MFA raise `high` severity findings.
- Review reports are exported as JSON + Markdown and recorded in `audit_logs`.

---

## Recovery & Rollback Procedures

1. **Signing Key Missing**: Generate a 256-bit key (`python -c "import secrets; print(secrets.token_hex(32))"`) and store it in HashiCorp Vault or the Kubernetes Secret referenced by `AUDIT_SIGNING_KEY`.
2. **S3 Upload Failure**: `RetentionPolicy.archive_old_logs()` never prunes hot database rows when `put_object` raises an exception; failed batches increment `errors` and remain safely in PostgreSQL for the next idempotent retry.
3. **Disable Audit Temporarily**: Set `compliance.audit.enabled: false` in `config/compliance.yaml` or uninstall CronJobs with `--set compliance.enabled=false`.

---

## Related Documentation

- [SOC2 Trust Service Criteria Control Mapping](soc2-controls.md)
- [GDPR Compliance Procedures](gdpr-compliance.md)

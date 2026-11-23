# Database Restore & PITR Quick-Reference Runbook

Operator quick reference for executing database recovery during P0/P1 incidents or quarterly DR drills. See [Disaster Recovery Plan](disaster-recovery-plan.md) for full governance, retention tiers, and escalation contacts.

---

## Quick Commands

```bash
# Inspect available encrypted backups and WAL ranges
pgbackrest --stanza=product-value-system info

# Dry-run latest restore (validates flags without modifying cluster)
./scripts/restore-database.sh --target latest --dry-run

# Execute full restore from local repo1 (latest consistent backup)
./scripts/restore-database.sh --target latest

# Execute cross-region restore from S3 repo2 (us-west-2) during region outage
./scripts/restore-database.sh --repo 2 --target latest

# Execute Point-in-Time Recovery (PITR) to exact UTC timestamp
./scripts/pitr-recovery.sh "2026-11-23 10:00:00"
```

---

## Post-Restore Verification Checklist

1. Confirm PostgreSQL readiness: `kubectl get pods -l app=postgres -n product-value-system`.
2. Confirm table row counts and `PRAGMA` / `pg_database_size` sanity checks.
3. Run HMAC audit log integrity verification: `POST /api/v1/audit/verify` -> `"integrity_verified": true`.
4. Confirm `/health/ready` returns `200 OK` and run `make test-integration`.
5. Record measured RTO and RPO in the incident postmortem / quarterly DR drill report.

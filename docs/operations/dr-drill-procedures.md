# Quarterly Disaster Recovery (DR) Drill Procedures

**Owner**: Database Team & SRE Lead  
**Cadence**: Quarterly (Second Wednesday, 10:00–12:00 UTC)  
**SLA Targets**: **RTO `< 1 hour`** · **RPO `< 15 minutes`** · **Zero Unverified Backups**  
**Last Updated**: 2026-11-23

---

## 1. Objective & Compliance Mandate

Quarterly DR drills empirically verify that our encrypted PostgreSQL backups, WAL archives, cross-region S3 replication (`us-east-1 -> us-west-2` with Object Lock), and restore scripts ([restore-database.sh](../../scripts/restore-database.sh), [pitr-recovery.sh](../../scripts/pitr-recovery.sh)) recover production-equivalent data inside contractual **RTO (`< 1h`)** and **RPO (`< 15m`)** SLAs, satisfying SOC2 CC7.5 / A1.3 backup testing requirements.

---

## 2. Quarterly Drill Rotation Matrix

| Quarter | Drill Scenario | Primary Script / Tool | Key Validation |
|---|---|---|---|
| **Q1** | Full Database Restore to Clean Cluster (`us-east-1` snapshot) | `scripts/restore-database.sh` | Checksum, decryption, row counts, API smoke tests |
| **Q2** | Point-in-Time Recovery (PITR) reversing accidental `DROP TABLE` | `scripts/pitr-recovery.sh` | Exact target timestamp recovery (`RPO < 15m`) |
| **Q3** | Cross-Region DR Failover from `us-west-2` S3 Replica | `pgbackrest --repo=2 restore` | Region-outage simulation + DNS/Helm redeploy |
| **Q4** | Full-Stack Chaos + DR Combined Exercise (Primary + Audit Verify) | `restore-database.sh` + `/api/v1/audit/verify` | Cryptographic audit log integrity post-restore |

---

## 3. Pre-Drill Checklist (T-24 Hours)

- [ ] Provision isolated DR drill namespace (`pvs-dr-drill`) — **NEVER overwrite production data**
- [ ] Confirm latest encrypted backup and cross-region S3 replica exist (`pgbackrest --stanza=pvs info`)
- [ ] Confirm drill participants assigned: **Drill Lead**, **Database Operator**, **Verifier**, **Compliance Scribe**
- [ ] Announce drill window in `#operations` and `#incidents`
- [ ] Verify automated DR unit/drill suite passes locally: `pytest tests/persistence/test_disaster_recovery.py -v`

---

## 4. Step-by-Step Drill Execution (2 Hours)

### Phase 1: Capture Pre-Failure Canary State (`00–15 min`)
1. Record a unique canary marker row and timestamp `T_canary` in the staging/drill PostgreSQL instance.
2. Force a WAL switch (`SELECT pg_switch_wal();`) and verify WAL segment archived to S3.
3. For Q2 PITR drills, record `T_target` right before injecting a simulated destructive error (`DROP TABLE drill_canary;`).

### Phase 2: Execute Timed Recovery (`15–75 min`)
Start the RTO stopwatch (`T_start`):
```bash
# Option A: Full snapshot restore into isolated pvs-dr-drill namespace
NAMESPACE=pvs-dr-drill bash scripts/restore-database.sh --verify

# Option B: Point-in-Time Recovery to exact UTC timestamp
NAMESPACE=pvs-dr-drill bash scripts/pitr-recovery.sh --target-time "${T_target}"
```
Record `T_db_ready` when PostgreSQL accepts read/write queries and `T_app_ready` when application pods pass `bash scripts/deployment/smoke-tests.sh`.

### Phase 3: Data Integrity & Compliance Verification (`75–100 min`)
- [ ] Confirm canary marker row matches pre-failure checksum (`RPO` delta measured in seconds/minutes).
- [ ] Execute `POST /api/v1/audit/verify` on restored instance — verify `valid: true` and zero tampered rows.
- [ ] Execute `bash scripts/deployment/smoke-tests.sh --target-url <DRILL_URL>` — verify `6/6` smoke tests pass.
- [ ] Compute **Measured RTO** (`T_app_ready - T_start`) and **Measured RPO** (`T_failure - T_last_recovered_txn`).

### Phase 4: Cleanup & Compliance Report (`100–120 min`)
1. Tear down isolated `pvs-dr-drill` namespace and temporary PVCs.
2. Complete the Drill Scorecard below, attach redacted terminal logs, and file under private compliance evidence for SOC2 audits.
3. Promote any bottleneck (e.g., slow restore download, missing IAM permission, stale doc command) to the [SRE Kaizen Board](../../.github/projects/kaizen-board.yml).

---

## 5. DR Drill Evidence Scorecard

| Metric | SLA Target | Measured Result | Pass / Fail |
|---|---|---|---|
| **RTO (Full Service Restoration)** | `< 60 minutes` | `[XX min]` | `[PASS / FAIL]` |
| **RPO (Data Loss Window)** | `< 15 minutes` | `[XX sec]` | `[PASS / FAIL]` |
| **Backup Decryption & Checksum** | `100% valid` | `[Verified]` | `[PASS / FAIL]` |
| **Audit Trail HMAC Integrity** | `100% valid` | `[Verified]` | `[PASS / FAIL]` |
| **Smoke Test Suite (`smoke-tests.sh`)** | `6/6 passed` | `[X/6]` | `[PASS / FAIL]` |

---

## Related Documentation

- [Disaster Recovery Plan](disaster-recovery-plan.md)
- [Database Restore Procedures](restore-procedures.md)
- [Emergency Data Loss Playbook](../runbooks/emergency-data-loss.md)
- [SOC2 Controls Documentation](../compliance/soc2-controls.md)
- [SRE Team Rituals](team-rituals.md)

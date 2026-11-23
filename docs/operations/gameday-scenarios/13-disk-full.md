# GameDay Scenario: Disk Space Exhaustion (PVC Pressure)

**Scenario ID**: `GAMEDAY-033`  
**Category**: Resource Exhaustion  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: Expandable StorageClass (`allowVolumeExpansion: true`) and audit archival CronJob in staging

---

## Objective
Verify that PVC disk usage alerts fire ahead of `100%` saturation and that engineers can expand a staging PVC online and execute audit archival without downtime.

**Success Criteria**:
- Warning triggers at `>= 80%` PVC usage before PostgreSQL write failure
- Online PVC expansion (`kubectl patch pvc`) increases filesystem capacity with zero downtime
- Audit archival CronJob reclaims expired records cleanly

---

## Pre-GameDay Checklist
- [ ] Verify staging StorageClass allows expansion: `kubectl get sc`
- [ ] Review [Disk Full Runbook](../../runbooks/incident-disk-full.md)

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl get pvc -n product-value-system-staging
```

### Phase 2: Chaos Injection (10 min)
Create a temporary test file inside an isolated staging scratch volume (`dd if=/dev/zero of=/tmp/gameday-fill.bin bs=1M count=...`) to cross `82%` utilization (never reach `100%` on shared nodes).

### Phase 3: Observation (15 min)
Confirm Prometheus PVC utilization alert fires and practice executing [Disk Full Runbook](../../runbooks/incident-disk-full.md) online expansion steps.

### Phase 4: Recovery (15 min)
Remove temporary fill file, verify expanded PVC status, and confirm PostgreSQL writes healthy.

### Phase 5: Retrospective (15 min)
Review WAL retention sizing and archival schedule frequency.

---

## Cleanup
Remove `/tmp/gameday-fill.bin` from staging pod and confirm `df -h` back below `50%`.

---

## Expected Outcomes
- **Green**: Alert fired `>= 80%`, online expansion succeeded with zero downtime.
- **Yellow**: Expansion required pod restart to resize filesystem.
- **Red**: StorageClass lacked `allowVolumeExpansion` or database halted unexpectedly.

## Failure Mode Analysis
Verify CSI driver online resize capability and `cronjob-archive.yaml` schedule health.

## Related Scenarios
- [GAMEDAY-011: Database Primary Failure](05-database-primary-failure.md) · [Disk Full Runbook](../../runbooks/incident-disk-full.md)

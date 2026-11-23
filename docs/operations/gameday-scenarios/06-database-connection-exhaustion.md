# GameDay Scenario: Database Connection Pool Exhaustion

**Scenario ID**: `GAMEDAY-012`  
**Category**: Database Resilience  
**Difficulty**: Medium  
**Duration**: 45 minutes  
**Prerequisites**: PgBouncer transaction pooling + `/health/metrics-summary` deployed in staging

---

## Objective
Validate that PgBouncer transaction pooling absorbs high client concurrency cleanly and that on-call engineers diagnose and mitigate connection saturation quickly.

**Success Criteria**:
- PgBouncer queues bursts without exhausting PostgreSQL `max_connections`
- `/health/metrics-summary` surfaces pool utilization accurately
- Runbook mitigation clears queue saturation within `< 10 minutes`

---

## Pre-GameDay Checklist
- [ ] Inspect baseline pool stats via `/health/metrics-summary`
- [ ] Review [Connection Exhaustion Runbook](../../runbooks/incident-database-connection-exhaustion.md)

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
kubectl exec -n product-value-system-staging deploy/product-value-system -- \
  curl -fsS http://localhost:8000/health/metrics-summary
```

### Phase 2: Chaos Injection (10 min)
Run high-concurrency k6 stress burst (`k6 run tests/load/k6-stress-test.js`) against staging with temporarily reduced PgBouncer `default_pool_size` to trigger queuing.

### Phase 3: Observation (10 min)
Inspect PgBouncer `SHOW POOLS;` (`cl_waiting`, `sv_active`), API latency p99, and `SLOLatencyP99High` alert timing.

### Phase 4: Recovery (10 min)
Follow [Connection Exhaustion Runbook](../../runbooks/incident-database-connection-exhaustion.md) to restore pool headroom and verify `cl_waiting == 0`.

### Phase 5: Retrospective (10 min)
Capture optimal `default_pool_size` vs `max_client_conn` ratios on the Kaizen board.

---

## Cleanup
Restore staging PgBouncer Helm values to baseline (`helm rollback`).

---

## Expected Outcomes
- **Green**: Zero PostgreSQL crashes; saturation visible in metrics and mitigated `< 10 min`.
- **Yellow**: Elevated p99 latency (`> 2s`) before pool tuning takes effect.
- **Red**: Unbounded connections crash PostgreSQL or `/health/ready` fails to signal degradation.

## Failure Mode Analysis
Verify `statement_cache_size=0` and `pool_mode=transaction` in PgBouncer and `PostgresDatabase`.

## Related Scenarios
- [GAMEDAY-013: Slow Queries](07-database-slow-queries.md) · [DB Connection Exhaustion Runbook](../../runbooks/incident-database-connection-exhaustion.md)

# GameDay Scenario: Database Slow Queries

**Scenario ID**: `GAMEDAY-013`  
**Category**: Database Resilience  
**Difficulty**: Medium  
**Duration**: 45 minutes  
**Prerequisites**: PostgreSQL `pg_stat_statements` enabled in staging

---

## Objective
Verify that slow SQL queries are quickly identified using `scripts/runbook-automation/diagnose-slow-queries.sh`, bounded by `statement_timeout`, and isolated before saturating all worker pools.

**Success Criteria**:
- `diagnose-slow-queries.sh` pinpoints offending query within `< 2 minutes`
- `statement_timeout` aborts runaway query before cascading API timeout
- Jaeger traces clearly attribute latency to the database span

---

## Pre-GameDay Checklist
- [ ] Confirm `pg_stat_statements` active in staging
- [ ] Review [Slow Queries Runbook](../../runbooks/incident-slow-queries.md)

---

## Procedure

### Phase 1: Baseline (5 min)
```bash
NAMESPACE=product-value-system-staging bash scripts/runbook-automation/diagnose-slow-queries.sh --dry-run
```

### Phase 2: Chaos Injection (10 min)
Execute controlled staging slow queries (`SELECT pg_sleep(4);` across 5 concurrent sessions) against the staging database while synthetic API traffic runs.

### Phase 3: Observation (10 min)
Run `diagnose-slow-queries.sh --threshold-ms 100` and inspect Jaeger SQL spans + `pg_stat_activity`.

### Phase 4: Recovery (10 min)
Terminate synthetic slow sessions via `pg_cancel_backend()` and confirm API p99 drops `< 1s`.

### Phase 5: Retrospective (10 min)
Review whether slow-query alerting fired early enough and document index/timeout gaps.

---

## Cleanup
Ensure zero `pg_sleep` backends remain in `pg_stat_activity`.

---

## Expected Outcomes
- **Green**: Query diagnosed `< 2 min`, bounded by timeout, API error rate `< 1%`.
- **Yellow**: Diagnosed cleanly but took `> 5 min` or no proactive alert fired.
- **Red**: Slow queries exhausted PgBouncer pool causing widespread `504` timeouts.

## Failure Mode Analysis
Check `statement_timeout` defaults and missing Prometheus slow-query alert thresholds.

## Related Scenarios
- [GAMEDAY-012: Connection Exhaustion](06-database-connection-exhaustion.md) · [Slow Queries Runbook](../../runbooks/incident-slow-queries.md)

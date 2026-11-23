# GameDay Scenario: Network Latency Injection

**Scenario ID**: `GAMEDAY-021`  
**Category**: Network Resilience  
**Difficulty**: Easy  
**Duration**: 45 minutes  
**Prerequisites**: Chaos Mesh installed in staging, Jaeger tracing active (PROD-007)

---

## Objective
Validate that injecting `250ms–1000ms` network latency with jitter does not trigger retry storms, cascading timeouts, or broken circuit breakers, and that Jaeger traces pinpoint the delayed hop immediately.

**Success Criteria**:
- Zero cascading `5xx` errors (`< 1%`) under `250ms` delay
- Jaeger flame graph isolates injected network latency within `< 2 minutes`
- `SLOLatencyP99High` fires predictably when delay pushes p99 `> 1s`

---

## Pre-GameDay Checklist
- [ ] Open Jaeger UI (`product-value-system`) and Grafana `pvs-slo-dashboard`
- [ ] Preview chaos manifest: `bash scripts/gameday/inject-latency.sh --dry-run`

---

## Procedure

### Phase 1: Baseline (5 min)
Record p50/p95/p99 latency from `/health/metrics-summary` and Grafana.

### Phase 2: Chaos Injection (5 min)
```bash
bash scripts/gameday/inject-latency.sh \
  --namespace product-value-system-staging \
  --latency 250ms --jitter 50ms --duration 5m
```

### Phase 3: Observation (15 min)
Inspect Jaeger waterfall traces, retry counter metrics, and Prometheus alert state (`SLOLatencyP99High`).

### Phase 4: Recovery (5 min)
```bash
bash scripts/gameday/inject-latency.sh --namespace product-value-system-staging --cleanup
```
Verify p99 returns `< 1s` within 2 minutes.

### Phase 5: Retrospective (15 min)
Review client/upstream timeout budgets and jittered exponential backoff configuration.

---

## Cleanup
```bash
bash scripts/gameday/inject-latency.sh --namespace product-value-system-staging --cleanup
```

---

## Expected Outcomes
- **Green**: Graceful latency absorption, accurate Jaeger attribution, zero retry storm.
- **Yellow**: Minor `504` spike during latency burst; recovers automatically on cleanup.
- **Red**: Unbounded retries saturate thread/connection pools or fail to recover post-cleanup.

## Failure Mode Analysis
Audit timeout hierarchies (Ingress > FastAPI > DB/LLM client) and `CircuitBreaker` thresholds.

## Related Scenarios
- [GAMEDAY-022: Network Partition](09-network-partition.md) · [High API Latency Runbook](../../runbooks/incident-high-latency.md)

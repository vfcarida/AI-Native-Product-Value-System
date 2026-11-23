# Runbook: High API Latency

**Severity**: P2 (Warning) / P1 (Critical if `p99 > 5s`)  
**Alert**: `SLOLatencyP99High`, `SLOLatencyP99Critical`, `SLOLatencyP95Sustained`  
**SLO Impact**: SLO-02 Latency (`p99 < 1s`)  
**Estimated MTTR**: 10–20 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team

---

## 1. Symptoms
- [ ] `SLOLatencyP99High` (`> 1s` for 5m) or `SLOLatencyP99Critical` (`> 5s` for 2m) fires
- [ ] Grafana SLO Dashboard shows elevated p95/p99 latency
- [ ] Jaeger flame graphs show prolonged request spans

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Check pod CPU/memory utilization
kubectl top pods -n product-value-system -l app.kubernetes.io/name=product-value-system

# 2. Inspect live connection pool & average latency
kubectl exec -n product-value-system deploy/product-value-system -- \
  curl -fsS http://localhost:8000/health/metrics-summary

# 3. Check slow PostgreSQL queries (>100ms)
./scripts/runbook-automation/diagnose-slow-queries.sh --threshold-ms 100
```

### Decision Tree
- Connection pool saturated (`checked_out >= size`) -> [Connection Exhaustion](incident-database-connection-exhaustion.md)
- Pod CPU/memory `> 80%` -> Section 3.A
- Slow SQL queries `> 100ms` -> [Slow Queries](incident-slow-queries.md)
- Upstream LLM span dominates Jaeger flame graph -> [LLM Provider Outage](incident-llm-provider-outage.md)

---

## 3. Remediation

### 3.A: Scale Application Pods
```bash
./scripts/runbook-automation/auto-scale-up.sh --increment 2
```
Verify p99 drops `< 1s` in Grafana after 2 minutes. **Rollback**: HPA scales down automatically after 5-minute stabilization window.

### 3.B: Trace-Driven Bottleneck Mitigation
Follow [High-Latency Trace Runbook](trace-high-latency.md) in Jaeger (`minDuration=1s`) to pinpoint slow model calls or sequential agent steps; switch low/medium tasks to faster tiers in `config/routing.yaml` if needed.

---

## 4. Prevention
- [ ] Tune HPA CPU target (`70% -> 60%`) if traffic spikes routinely outpace scale-up
- [ ] Enable L2 Redis prompt caching (`config/caching.yaml`) for repeated analyses
- [ ] Run nightly k6 regression gate ([Load Testing Guide](../operations/load-testing-guide.md))

---

## 5. Escalation
If p99 remains `> 1s` after 20 minutes, escalate via PagerDuty to Backup On-Call and post slow Jaeger `traceID` links plus `kubectl top pods` output in `#incidents`.

---

## 6. References
- [High-Latency Trace Runbook](trace-high-latency.md)
- [Distributed Tracing Guide](../operations/tracing-guide.md)
- [SLO Definitions](../operations/slos.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Execute `k6 run tests/load/k6-spike-test.js` in `pvs-load-test` and validate `auto-scale-up.sh` restores p99 `< 1s`.

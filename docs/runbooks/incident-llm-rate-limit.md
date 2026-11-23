# Runbook: LLM API Rate Limit Exhaustion (HTTP 429)

**Severity**: P2 (Warning) / P1 (Critical if causing widespread agent failures)  
**Alert**: `SLOLatencyP99High`, `AgentSuccessRateLow`, `LLMRateLimitExceeded`  
**SLO Impact**: SLO-02 Latency (`p99 < 1s`), SLO-04 Agent Success Rate (`>= 95%`)  
**Estimated MTTR**: 10–20 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team / FinOps Team

---

## 1. Symptoms
- [ ] Application logs show repeated `429 Too Many Requests`, `rate_limit_exceeded`, or `tokens_per_minute` errors
- [ ] Exponential backoff retries cause p99 latency spikes in Jaeger traces
- [ ] `AgentSuccessRateLow` fires as retries exhaust maximum attempts
- [ ] Sudden burst of concurrent batch evaluations or brownfield scans

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Count 429 responses by provider/model over recent logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=500 \
  | grep -iE "429|rate.?limit|quota|too many requests" | tail -n 30

# 2. Check current orchestrator rate limits and active concurrency
grep -nE "rate_limit|concurrency|max_parallel" config/orchestrator.yaml

# 3. Check prompt cache hit ratio (cache misses amplify upstream RPM/TPM)
kubectl exec -n product-value-system deploy/product-value-system -- \
  curl -fsS http://localhost:8000/health/metrics-summary
```

### Decision Tree
- Cache hit ratio dropped sharply (cache miss storm) -> [Cache Degradation](incident-cache-degradation.md)
- Runaway client or batch job exhausting TPM/RPM -> Section 3.A
- Legitimate growth exceeding single-provider quota -> Section 3.B (Load-balance across providers)
- Hard monthly spend cap reached (not RPM/TPM) -> [Budget Exhaustion](incident-budget-exhaustion.md)

---

## 3. Remediation

### 3.A: Throttle Concurrency & Pause Non-Critical Batch Jobs
```bash
# Suspend non-urgent batch evaluation CronJobs if running
kubectl get cronjobs -n product-value-system

# Lower orchestrator per-pod concurrency / rate limit ceiling via Helm
helm upgrade product-value-system deploy/helm/product-value-system \
  -n product-value-system --reuse-values \
  --set env.PVS_LLM_RATE_LIMIT_PER_MINUTE=60 \
  --wait --timeout 5m
```
Verify `429` log frequency drops to `0` and retry latencies stabilize. **Rollback**: Restore normal rate limit after burst clears.

### 3.B: Split Traffic Across Secondary Provider / Tier
Shift `low` and `medium` complexity agent tasks to secondary provider or higher-quota tier (`config/routing_multiprovider.yaml`), and request permanent RPM/TPM quota increase from the primary LLM vendor.

---

## 4. Prevention
- [ ] Enable L2 Redis prompt caching (`config/caching.yaml`) to avoid duplicate upstream token consumption
- [ ] Schedule large `batch-evaluations` off-peak using provider Batch APIs ([Batch Evaluations](../batch-evaluations.md))
- [ ] Alert when upstream RPM/TPM consumption reaches `75%` of contractual quota

---

## 5. Escalation
If `429` errors persist across multiple providers or require an emergency vendor quota increase, escalate to Engineering Lead + FinOps Owner to contact provider account support.

---

## 6. References
- [Model Routing Guide](../model-routing.md)
- [Token Caching](../token-caching.md)
- [Cost Management](../COST_MANAGEMENT.md)
- [LLM Provider Outage](incident-llm-provider-outage.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `tests/load/k6-spike-test.js` against staging with a lowered rate-limit ceiling, confirm backpressure and secondary provider routing prevent cascading `5xx` errors.

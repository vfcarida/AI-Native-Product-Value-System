# GameDay Scenario: Anthropic API High Latency & Circuit Breaker

**Scenario ID**: `GAMEDAY-042`  
**Category**: External Dependencies  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: Chaos Mesh `NetworkChaos`, Jaeger distributed tracing, `CircuitBreaker` enabled

---

## Objective
Validate that severe upstream LLM latency (`5s+` delay) on Anthropic triggers bounded timeouts, full-jitter retries, prompt cache hits where applicable, and clean circuit-breaker protection without exhausting API worker concurrency.

**Success Criteria**:
- Requests never hang indefinitely (strict adapter timeout enforced)
- Cached prompts continue serving from L1/L2 cache `< 50ms`
- Jaeger flame graph isolates upstream `llm.call` delay cleanly
- `SLOLatencyP95Sustained` fires and alerts on-call accurately

---

## Pre-GameDay Checklist
- [ ] Confirm prompt caching active in staging (`config/caching.yaml`)
- [ ] Review [LLM Rate Limit Runbook](../../runbooks/incident-llm-rate-limit.md) and [High-Latency Trace Runbook](../../runbooks/trace-high-latency.md)

---

## Procedure

### Phase 1: Baseline (5 min)
Capture baseline p95 LLM span latency and cache hit ratio from Jaeger and Grafana.

### Phase 2: Chaos Injection (5 min)
```bash
bash scripts/gameday/inject-latency.sh \
  --namespace product-value-system-staging \
  --latency 5000ms --jitter 500ms --duration 5m
```

### Phase 3: Observation (20 min)
Verify cached prompts bypass upstream delay, uncached requests respect timeout/retry ceilings, and worker pods remain responsive on `/health/live`.

### Phase 4: Recovery (10 min)
```bash
bash scripts/gameday/inject-latency.sh --namespace product-value-system-staging --cleanup
```
Verify latency and circuit breaker return to healthy baseline.

### Phase 5: Retrospective (20 min)
Review retry counts, timeout margins, and prompt cache hit-rate opportunities.

---

## Cleanup
```bash
bash scripts/gameday/inject-latency.sh --namespace product-value-system-staging --cleanup
```

---

## Expected Outcomes
- **Green**: Strict timeouts prevent worker starvation, cache serves repeated prompts fast, Jaeger pinpoints span.
- **Yellow**: Retries briefly amplified p99 latency before circuit breaker tripped.
- **Red**: Unbounded waits exhausted event loop/threads and failed `/health/live`.

## Failure Mode Analysis
Audit adapter timeout (`request_timeout_seconds`), retry max attempts, and fallback tier thresholds in `config/orchestrator.yaml`.

## Related Scenarios
- [GAMEDAY-041: OpenAI API Outage](14-openai-api-outage.md) · [High-Latency Trace Runbook](../../runbooks/trace-high-latency.md)

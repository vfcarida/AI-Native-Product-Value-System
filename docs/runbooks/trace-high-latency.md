# Runbook: Debugging High Latency with Distributed Traces

**Trigger**: Latency SLO breach (`p99 > 1s` warning or `p99 > 5s` critical — see [slos.md](../operations/slos.md)).  
**Owner**: On-Call SRE / Platform Engineering  
**Tooling**: Jaeger UI (`https://jaeger.product-value-system.internal`), Grafana SLO Dashboard

---

## 1. Identify Slow Traces in Jaeger

1. Open Jaeger UI (`kubectl port-forward -n jaeger svc/product-value-system-jaeger-query 16686:16686`).
2. Search with:
   - **Service**: `product-value-system`
   - **Lookback**: Last 1 hour
   - **Min Duration**: `1s`
   - **Limit**: `100`
3. Sort by **Longest First** and open 3–5 representative slow traces.

---

## 2. Analyze the Flame Graph & Waterfall

Identify which child span dominates the critical path:

| Dominant Span | Typical Root Cause | Key Span Tags to Inspect |
|---|---|---|
| `model.openai.*` / `model.anthropic.*` / `model.gemini.*` | Upstream LLM latency, oversized prompts, or retry loops | `model.name`, `model.tokens.total`, `model.cost.usd`, `error.type` |
| `agent.execution` / `agent.step.*` | High iteration count or sequential steps that should parallelize | `agent.name`, `agent.iterations`, `pvs.run_id` |
| `agent.tool.*` | Slow external API or sandbox startup overhead | `tool.name`, `tool.authorized`, `tool.has_error` |
| `database.*` | Missing index or connection-pool exhaustion | `db.system`, `db.statement` |

---

## 3. Mitigation Playbook

- **Model API Latency**:
  - Verify complexity routing in `config/routing.yaml` is routing low/medium tasks to fast tiers (`gpt-4o-mini`, `claude-3-haiku`, `gemini-2.0-flash`).
  - Verify prompt cache hit rate in `/metrics` (`pvs_cache_hits_total`).
- **Excessive Agent Iterations**:
  - Inspect `agent.iterations` and cap budget iterations in harness configuration.
- **Tracing Overhead Suspected (>5% latency)**:
  - Lower sampling rate immediately: set `OTEL_TRACES_SAMPLER_ARG=0.01` (`1%`) or disable SDK export completely via `OTEL_SDK_DISABLED=true` (zero application restart errors).

---

## 4. Verify Resolution

1. Re-run the affected request or agent workflow.
2. Confirm the new trace duration in Jaeger drops below the `1s` p99 target.
3. Confirm the Grafana SLO burn-rate alert returns to `OK`.

---

## Related Documentation

- [Distributed Tracing Operational Guide](../operations/tracing-guide.md)
- [Production SLOs & Alerting Rules](../operations/slos.md)

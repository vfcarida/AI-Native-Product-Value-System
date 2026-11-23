# Runbook: Upstream LLM Provider Outage

**Severity**: P1 (Critical if primary provider down without automatic fallback) / P2 (Warning if degraded)  
**Alert**: `AgentSuccessRateCritical`, `SLOErrorRateHigh`, `SLOLatencyP99High`  
**SLO Impact**: SLO-04 Agent Success Rate (`>= 95%`), SLO-02 Latency (`p99 < 1s`)  
**Estimated MTTR**: 5–15 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Engineering / SRE Team

---

## 1. Symptoms
- [ ] Upstream LLM calls return HTTP `500`, `502`, `503`, `504`, or connection timeouts
- [ ] Jaeger traces show `llm.call` spans failing or timing out across multiple agents
- [ ] `AgentSuccessRateLow` / `AgentSuccessRateCritical` fires
- [ ] Provider status pages (OpenAI, Anthropic, Google Cloud Vertex/Gemini) report active incidents

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect recent LLM adapter errors in application logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=150 \
  | grep -iE "openai|anthropic|gemini|provider|circuit|timeout|503|502"

# 2. Check current active provider routing configuration
kubectl get configmap -n product-value-system -o yaml | grep -A 25 "routing"

# 3. Verify external HTTPS reachability to provider endpoints (no secrets printed)
kubectl exec -n product-value-system deploy/product-value-system -- \
  python -c "import urllib.request; [print(u, urllib.request.urlopen(u, timeout=5).status) for u in ['https://status.openai.com', 'https://status.anthropic.com']]"
```

### Decision Tree
- HTTP `429 Too Many Requests` (quota exceeded, provider healthy) -> [LLM Rate Limit](incident-llm-rate-limit.md)
- DNS / TCP egress blocked by sandbox firewall -> [Sandbox Egress Block](incident-sandbox-egress-block.md)
- Primary LLM provider returning `5xx` / timeouts -> Section 3.A (Failover to secondary provider)
- All cloud LLM providers unreachable -> Section 3.B (Graceful degradation & circuit breaker mode)

---

## 3. Remediation

### 3.A: Fail Over Routing to Healthy Secondary Provider
```bash
# Switch preferred provider via Helm environment/config override (e.g., openai -> anthropic or gemini)
helm upgrade product-value-system deploy/helm/product-value-system \
  -n product-value-system --reuse-values \
  --set env.PVS_PREFERRED_LLM_PROVIDER=anthropic \
  --wait --timeout 5m

# Verify agent runs recover on the secondary provider
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=50
```
Verify `AgentSuccessRate` recovers `>= 95%` and `5xx` error rate drops `< 1%`. **Rollback**: Restore primary provider once upstream status page confirms full recovery (`helm rollback product-value-system -n product-value-system`).

### 3.B: Activate Cache-First & Reduced-Tier Graceful Degradation
If multiple providers are impaired, ensure prompt caching (`config/caching.yaml`) is active and route non-critical requests to `fast`/local fallback models defined in `config/routing_multiprovider.yaml`. Post customer status notice in `#incidents`.

---

## 4. Prevention
- [ ] Maintain active credentials and verified quotas for at least two LLM providers in HashiCorp Vault
- [ ] Keep `config/routing_multiprovider.yaml` fallback chains tested in CI (`tests/unit/test_model_router.py`)
- [ ] Tune adapter retry backoff and circuit-breaker thresholds in `config/orchestrator.yaml`

---

## 5. Escalation
If automatic and manual provider failover both fail for `> 10 minutes`, escalate to Engineering Lead to coordinate customer communication and activate degraded/read-only mode.

---

## 6. References
- [Model Routing Guide](../model-routing.md)
- [Agent Success Rate Drop](incident-agent-success-drop.md)
- [LLM Rate Limit](incident-llm-rate-limit.md)
- [Distributed Tracing Guide](../operations/tracing-guide.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Point staging primary adapter endpoint to a mock `503` responder, verify `ModelRouter` fails over to secondary provider within `< 30s`, and confirm `AgentSuccessRate` stays `>= 95%`.

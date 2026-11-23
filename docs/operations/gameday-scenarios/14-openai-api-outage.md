# GameDay Scenario: OpenAI API Outage & Provider Failover

**Scenario ID**: `GAMEDAY-041`  
**Category**: External Dependencies  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: Multi-provider routing (`config/routing_multiprovider.yaml`) configured in staging

---

## Objective
Validate that a complete OpenAI API outage (`503` / unreachable) triggers clean failover to Anthropic/Gemini via `ModelRouter` and keeps `AgentSuccessRate >= 95%`.

**Success Criteria**:
- `CircuitBreaker` trips on OpenAI adapter without infinite retry loops
- Traffic fails over to secondary provider within `< 30 seconds`
- `AgentSuccessRate` stays `>= 95%` during the outage window

---

## Pre-GameDay Checklist
- [ ] Verify secondary provider credentials valid in staging Vault / Secret
- [ ] Review [LLM Provider Outage Runbook](../../runbooks/incident-llm-provider-outage.md)

---

## Procedure

### Phase 1: Baseline (5 min)
Run staging discovery/spec workflow and confirm baseline model routing in Jaeger `llm.call` spans.

### Phase 2: Chaos Injection (5 min)
Block outbound traffic to `api.openai.com` in staging via Chaos Mesh `NetworkChaos` or route primary staging adapter endpoint to a mock `503` responder.

### Phase 3: Observation (20 min)
Verify Jaeger spans show primary error followed by secondary provider completion, check `AgentSuccessRateLow` alert behavior, and measure failover latency.

### Phase 4: Recovery (10 min)
Remove chaos rule / restore primary endpoint and verify `CircuitBreaker` transitions `OPEN -> HALF_OPEN -> CLOSED` cleanly.

### Phase 5: Retrospective (20 min)
Compare cost and latency deltas on secondary provider and update routing fallback weights if needed.

---

## Cleanup
```bash
kubectl delete networkchaos pvs-gameday-openai-block -n product-value-system-staging --ignore-not-found
```

---

## Expected Outcomes
- **Green**: Automatic failover `< 30s`, `AgentSuccessRate >= 95%`, automatic recovery when OpenAI returns.
- **Yellow**: Manual `PVS_PREFERRED_LLM_PROVIDER` Helm override needed; completed `< 10 min`.
- **Red**: Agent pipeline hung or returned ungraceful `500` errors to all callers.

## Failure Mode Analysis
Inspect `ModelRouter` fallback chain (`config/routing_multiprovider.yaml`) and `CircuitBreaker` failure thresholds.

## Related Scenarios
- [GAMEDAY-042: Anthropic API Latency](15-anthropic-api-latency.md) · [LLM Provider Outage Runbook](../../runbooks/incident-llm-provider-outage.md)

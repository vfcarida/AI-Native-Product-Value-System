# Runbook: Agent Completion Success Rate Drop

**Severity**: P2 (`< 95%`) / P1 (`< 85%`)  
**Alert**: `AgentSuccessRateLow`, `AgentSuccessRateCritical`  
**SLO Impact**: SLO-04 Agent Completion Rate (`> 95%`)  
**Estimated MTTR**: 15–30 minutes  
**Last Updated**: 2026-11-23  
**Owner**: AI Platform Engineering

---

## 1. Symptoms
- [ ] `AgentSuccessRateLow` or `AgentSuccessRateCritical` fires
- [ ] Increased `agent.failed` events in compliance audit logs
- [ ] Elevated `SecurityError`, `BudgetExhausted`, or schema validation failures

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect recent failed agent runs via CLI / logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --since=15m | grep -iE "agent.*fail|ValidationError|BudgetExhausted|SecurityError" | tail -n 40

# 2. Query Jaeger for failed agent spans (otel.status_code=ERROR)
# Service: product-value-system, Operation: agent.execute, Tags: otel.status_code=ERROR
```

### Decision Tree
- Upstream LLM timeouts/errors -> [LLM Provider Outage](incident-llm-provider-outage.md)
- FinOps budget limit reached -> [Budget Exhaustion](incident-budget-exhaustion.md)
- Sandbox blocked external API -> [Sandbox Egress Block](incident-sandbox-egress-block.md)
- Prompt/model regression after release -> [Rollback Procedure](rollback-procedure.md)

---

## 3. Remediation

### 3.A: Enable Fallback Routing Tier or Roll Back Model Snapshot
1. Verify `routing.quality_check.fallback_on_failure: true` in `config/routing.yaml`.
2. Switch `preferred_provider` to healthy backup provider (e.g., Anthropic <-> OpenAI <-> Gemini) or execute [Rollback Procedure](rollback-procedure.md) if caused by a prompt/schema deploy.
3. Freeze feature deployments per SLO policy until 10m completion rate recovers `> 95%`.

---

## 4. Prevention
- [ ] Run golden dataset evaluation suite (`make test`) before updating prompts or models
- [ ] Monitor `ModelRouter` fallback rate and schema-compliance metrics

---

## 5. Escalation
Escalate to AI Platform Lead after 20 minutes; include failing agent names, error types, and Jaeger trace IDs in `#incidents`.

---

## 6. References
- [SLO Definitions](../operations/slos.md)
- [Model Routing Guide](../model-routing.md)
- [Rollback Procedure](rollback-procedure.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `pytest tests/unit/test_model_router.py tests/observability/test_tracing.py -v` verifying fallback routing and error span capture.

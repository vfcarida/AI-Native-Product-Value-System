# Runbook: Token Budget Exhaustion & Emergency Stop

**Severity**: P1 (Critical if production workflows halted) / P2 (Warning at `80%` threshold)  
**Alert**: `BudgetWarningThreshold`, `BudgetExhausted`, `EmergencyStopTriggered`  
**SLO Impact**: SLO-04 Agent Success Rate (`>= 95%`), FinOps Spend Control  
**Estimated MTTR**: 10–20 minutes  
**Last Updated**: 2026-11-23  
**Owner**: FinOps / SRE Team

---

## 1. Symptoms
- [ ] `BudgetGuard` rejects new agent runs with `BudgetExceededError` or `EmergencyStopActive`
- [ ] FinOps cost metrics cross warning (`80%`) or hard limit (`100%`) configured in `config/budget_limits.yaml`
- [ ] `AgentSuccessRateLow` fires while upstream LLM providers remain healthy
- [ ] Sudden spike in `llm.cost_usd` visible in Jaeger traces and Grafana FinOps dashboard

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect configured daily/monthly budget ceilings
cat config/budget_limits.yaml

# 2. Check recent budget-rejection or emergency-stop logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=200 \
  | grep -iE "budget|emergency_stop|cost_usd|token"

# 3. Identify highest-cost runs or runaway loops in audit/trace logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=300 \
  | grep -i "cost_usd" | tail -n 25
```

### Decision Tree
- Runaway retry loop, compromised API token, or abusive client -> Section 3.A (Engage Emergency Stop & block source)
- Legitimate customer growth exceeding daily/monthly budget cap -> Section 3.B (Approve budget bump & shift tiers)
- Cache miss storm inflating token spend -> [Cache Degradation](incident-cache-degradation.md)

---

## 3. Remediation

### 3.A: Halt Runaway Spend Immediately (Kill Switch)
```bash
# Activate hard FinOps kill switch across all pods if abusive/runaway spend is active
helm upgrade product-value-system deploy/helm/product-value-system \
  -n product-value-system --reuse-values \
  --set env.PVS_EMERGENCY_STOP=true \
  --wait --timeout 3m
```
Identify and suspend the offending job/tenant, inspect for credential abuse via [Emergency Security Breach](emergency-security-breach.md), then clear `PVS_EMERGENCY_STOP=false` once contained. **Rollback**: Set `env.PVS_EMERGENCY_STOP=false`.

### 3.B: Approve Budget Increase & Enforce Cost-Efficient Tier Routing
```bash
# 1. Confirm Finance / Engineering Manager approval in #incidents
# 2. Update budget ceiling via Helm / ConfigMap override and route medium tasks to economy tier
helm upgrade product-value-system deploy/helm/product-value-system \
  -n product-value-system --reuse-values \
  --set env.PVS_EMERGENCY_STOP=false \
  --set env.PVS_ROUTING_PROFILE=cost_optimized \
  --wait --timeout 5m
```
Verify agent workflows resume and hourly burn rate aligns with approved cap. **Rollback**: `helm rollback product-value-system -n product-value-system`.

---

## 4. Prevention
- [ ] Configure multi-stage budget alerts (`50%`, `75%`, `90%`) before `100%` hard cutoff in `config/budget_limits.yaml`
- [ ] Enforce per-run token ceilings (`max_tokens_per_run`) in `config/orchestrator.yaml`
- [ ] Maximize L2 prompt cache hit rate (`> 60%`) to minimize redundant input tokens ([Token Caching](../token-caching.md))

---

## 5. Escalation
If unauthorized usage or credential exfiltration caused the spend spike, immediately invoke [Emergency Security Breach](emergency-security-breach.md) and rotate LLM API keys via [Emergency Secret Revocation](../operations/runbooks/emergency-secret-revocation.md). For legitimate cap increases exceeding `$5,000/month`, escalate to VP of Engineering / Finance Owner.

---

## 6. References
- [Cost Management Guide](../COST_MANAGEMENT.md)
- [Model Routing Guide](../model-routing.md)
- [Token Caching](../token-caching.md)
- [LLM Rate Limit](incident-llm-rate-limit.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Lower staging daily cap to `$1.00`, trigger runs until `BudgetGuard` halts execution, verify clear rejection errors, and confirm lifting the cap restores execution without pod restarts.

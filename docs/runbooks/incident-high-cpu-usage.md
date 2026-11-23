# Runbook: High CPU Usage & Throttling

**Severity**: P2 (Warning) / P1 (Critical if causing `SLOLatencyP99Critical`)  
**Alert**: `PodCPUThrottled`  
**SLO Impact**: SLO-02 Latency (`p99 < 1s`), SLO-01 Availability (`99.9%`)  
**Estimated MTTR**: 10–15 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team

---

## 1. Symptoms
- [ ] `PodCPUThrottled` fires (CFS CPU throttling `> 25%` for 5m)
- [ ] `kubectl top pods` shows application pods pegged near CPU limit
- [ ] API p95/p99 latency climbs while downstream LLM latency remains normal
- [ ] HPA reaches `maxReplicas` or scales too slowly during sudden burst traffic

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Check live pod CPU utilization
kubectl top pods -n product-value-system --sort-by=cpu

# 2. Check HorizontalPodAutoscaler replica status and scaling events
kubectl get hpa -n product-value-system
kubectl describe hpa -n product-value-system product-value-system

# 3. Check request rate and active work via health summary
kubectl exec -n product-value-system deploy/product-value-system -- \
  curl -fsS http://localhost:8000/health/metrics-summary
```

### Decision Tree
- Legitimate traffic burst and HPA below `maxReplicas` -> Section 3.A
- HPA already pinned at `maxReplicas` (`10`) -> Section 3.B
- Single pod CPU-bound while peers are idle (hot loop / stuck regex) -> Recycle pod via [Pod CrashLoopBackOff](incident-pod-crashloop.md)

---

## 3. Remediation

### 3.A: Immediate Horizontal Scale-Out
```bash
# Increment replicas immediately without waiting for HPA polling lag
./scripts/runbook-automation/auto-scale-up.sh --increment 3 --max-replicas 10
```
Verify CPU utilization per pod drops `< 65%` and `PodCPUThrottled` clears within 5 minutes. **Rollback**: HPA automatically scales back down after the 5-minute stabilization window.

### 3.B: Raise HPA Ceiling or Container CPU Limit
```bash
# Temporarily raise HPA maxReplicas and container CPU limit if sustained load exceeds capacity
helm upgrade product-value-system deploy/helm/product-value-system \
  -n product-value-system --reuse-values \
  --set autoscaling.maxReplicas=15 \
  --set resources.limits.cpu=2000m \
  --wait --timeout 5m
```
Verify throttling drops `< 10%` and p99 latency returns `< 1s`. **Rollback**: `helm rollback product-value-system -n product-value-system`.

---

## 4. Prevention
- [ ] Profile CPU-heavy spans (secret detection regexes, PII scanning, AST analysis) via Jaeger ([Tracing Guide](../operations/tracing-guide.md))
- [ ] Run capacity planning (`python scripts/capacity-planning.py`) before major customer onboarding
- [ ] Tune HPA `targetCPUUtilizationPercentage` (`70% -> 60%`) if bursts consistently cause CFS throttling

---

## 5. Escalation
If CPU remains pegged after scaling to `15` replicas or indicates an infinite loop/ReDoS attack, escalate to Engineering Lead + Security On-Call via PagerDuty and inspect ingress rate limits.

---

## 6. References
- [High API Latency](incident-high-latency.md)
- [High Availability Architecture](../operations/ha-architecture.md)
- [Load Testing Guide](../operations/load-testing-guide.md)
- [SLO Definitions](../operations/slos.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `k6 run tests/load/k6-stress-test.js` in staging, observe HPA scaling and `PodCPUThrottled` metric behavior, and verify `auto-scale-up.sh` relieves throttling within 3 minutes.

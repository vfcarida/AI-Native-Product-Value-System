# Runbook: Pod OOM Kill & High Memory Utilization

**Severity**: P2 (Warning at `> 85%` limit) / P1 (Critical if recurring OOMKill causes downtime)  
**Alert**: `PodMemoryHigh`, `KubeContainerOOMKilled`  
**SLO Impact**: SLO-01 Availability (`99.9%`), SLO-02 Latency (`p99 < 1s`)  
**Estimated MTTR**: 15–25 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team

---

## 1. Symptoms
- [ ] `PodMemoryHigh` fires (container memory utilization `> 85%` of limit for 5m)
- [ ] Pod terminates with `Reason: OOMKilled` and `Exit Code: 137`
- [ ] Sudden in-flight request failures (`502`/`503`) correlated with pod restarts
- [ ] Sawtooth or monotonically increasing container RSS graph in Grafana

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect current pod memory usage vs limits
kubectl top pods -n product-value-system --sort-by=memory

# 2. Check OOMKilled events across namespace
kubectl get events -n product-value-system --sort-by='.lastTimestamp' | grep -i oom

# 3. Inspect container memory requests/limits and last termination state
kubectl describe pods -n product-value-system -l app.kubernetes.io/name=product-value-system \
  | grep -E "Name:|Limits:|memory:|OOMKilled|Exit Code"
```

### Decision Tree
- Traffic spike across all pods (even memory growth with high RPS) -> Section 3.A
- Memory limit undersized for current model/Presidio PII workload -> Section 3.B
- Monotonic RSS leak on idle pods after recent release -> Roll back via [Rollback Procedure](rollback-procedure.md)

---

## 3. Remediation

### 3.A: Scale Out Replicas to Distribute Memory Load
```bash
# Add replicas immediately so per-pod request concurrency drops
./scripts/runbook-automation/auto-scale-up.sh --increment 2

# Recycle any pods stuck near OOM threshold
./scripts/runbook-automation/restart-unhealthy-pods.sh
```
Verify per-pod memory utilization drops `< 75%` via `kubectl top pods`. **Rollback**: HPA scales down automatically after load subsides.

### 3.B: Increase Container Memory Limit via Helm
```bash
# Raise memory request/limit cleanly with zero-downtime rolling update
helm upgrade product-value-system deploy/helm/product-value-system \
  -n product-value-system --reuse-values \
  --set resources.requests.memory=1Gi \
  --set resources.limits.memory=4Gi \
  --wait --timeout 5m
```
Verify rollout completes and `PodMemoryHigh` clears. **Rollback**: `helm rollback product-value-system -n product-value-system`.

---

## 4. Prevention
- [ ] Run weekly 4-hour soak tests (`k6 run tests/load/k6-soak-test.js`) to detect memory leaks early
- [ ] Bound in-memory L1 cache entry count in `config/caching.yaml` and offload large prompt caches to Redis
- [ ] Keep HPA memory utilization target (`80%`) below `PodMemoryHigh` alert threshold (`85%`)

---

## 5. Escalation
If pods continue OOMKilling after raising limits to `4Gi` and scaling out, escalate to Engineering Lead to capture a Python `tracemalloc` heap snapshot and roll back the latest release.

---

## 6. References
- [Load Testing Guide](../operations/load-testing-guide.md)
- [Pod CrashLoopBackOff](incident-pod-crashloop.md)
- [High Availability Architecture](../operations/ha-architecture.md)
- [SLO Definitions](../operations/slos.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `tests/load/k6-soak-test.js` against staging with tightened memory limit (`512Mi`), verify `PodMemoryHigh` triggers before OOM, and confirm `auto-scale-up.sh` + Helm limit bump restores headroom.

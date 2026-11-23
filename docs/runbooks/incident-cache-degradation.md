# Runbook: Cache Degradation & Redis Outage

**Severity**: P2 (Warning) / P1 (Critical if cascading into `SLOLatencyP99Critical` or `429` storm)  
**Alert**: `CacheHitRateLow`, `RedisUnreachable`, `SLOLatencyP99High`  
**SLO Impact**: SLO-02 Latency (`p99 < 1s`), FinOps Token Cost Efficiency  
**Estimated MTTR**: 10–20 minutes  
**Last Updated**: 2026-11-23  
**Owner**: SRE Team

---

## 1. Symptoms
- [ ] Prompt/semantic cache hit ratio drops abruptly (`< 20%`)
- [ ] Application logs report `RedisConnectionError`, `TimeoutError`, or `OOM command not allowed`
- [ ] Upstream LLM token spend and p99 latency increase simultaneously
- [ ] Stale or corrupted cached entries produce incorrect agent outputs after a prompt template update

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Check Redis pod status and memory usage
kubectl get pods -n product-value-system -l app=redis
kubectl top pods -n product-value-system -l app=redis

# 2. Ping Redis and inspect memory / eviction stats
kubectl exec -n product-value-system deploy/redis -- redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human|evicted_keys"
kubectl exec -n product-value-system deploy/redis -- redis-cli INFO stats | grep -E "keyspace_hits|keyspace_misses"

# 3. Check application cache errors in logs
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=100 \
  | grep -iE "redis|cache"
```

### Decision Tree
- Stale/corrupt cache keys after schema or prompt change -> Section 3.A (Selective cache clear)
- Redis `used_memory` pegged at `maxmemory` with high evictions -> Section 3.A + increase Redis `maxmemory`
- Redis pod down / crashing -> Section 3.B (Restart Redis & verify L1 fallback)

---

## 3. Remediation

### 3.A: Safely Clear Stale Prompt Cache Entries
```bash
# Preview impact with dry-run first
./scripts/runbook-automation/clear-cache.sh --prefix "pvs:cache:*" --dry-run

# Clear targeted key prefix (avoid global FLUSHALL unless explicitly required)
./scripts/runbook-automation/clear-cache.sh --prefix "pvs:cache:*"
```
Verify stale responses cease and Redis memory headroom recovers (`< 75%` of `maxmemory`). **Rollback**: N/A (cache repopulates organically from subsequent live requests).

### 3.B: Recover Redis & Confirm Graceful L1 Fallback
```bash
# Restart unhealthy Redis pod if stuck
kubectl rollout restart deployment/redis -n product-value-system
kubectl rollout status deployment/redis -n product-value-system --timeout=3m

# Verify application health continues serving via L1 memory/SQLite fallback during restart
kubectl exec -n product-value-system deploy/product-value-system -- \
  curl -fsS http://localhost:8000/health/ready
```
Verify Redis responds `PONG` and cache hit ratio climbs back `> 50%`. **Rollback**: If Redis remains unstable, temporarily set `cache_backend=memory` in `config/caching.yaml` while investigating Redis PVC/node health.

---

## 4. Prevention
- [ ] Configure Redis `maxmemory-policy allkeys-lru` so cache fills evict oldest keys instead of returning `OOM`
- [ ] Include prompt template version hashes in cache keys so deployments never serve stale prompt outputs
- [ ] Monitor `redis_memory_used_bytes / redis_memory_max_bytes` and alert at `80%`

---

## 5. Escalation
If Redis outage triggers upstream LLM `429` throttling or budget burn, escalate to SRE Lead and follow [LLM Rate Limit](incident-llm-rate-limit.md) concurrently.

---

## 6. References
- [Token Caching Guide](../token-caching.md)
- [High API Latency](incident-high-latency.md)
- [LLM Rate Limit](incident-llm-rate-limit.md)
- [Budget Exhaustion](incident-budget-exhaustion.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `./scripts/runbook-automation/clear-cache.sh --prefix "pvs:test:*" --dry-run`, verify zero API `5xx` errors when Redis is momentarily stopped, and confirm automatic reconnection when Redis returns.

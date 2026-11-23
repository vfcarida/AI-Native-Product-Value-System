# Runbook: Sandbox Egress Block & Allowlist Failure

**Severity**: P2 (Warning for isolated allowlist miss) / P1 (Critical if blocking all LLM or package traffic)  
**Alert**: `SandboxEgressBlockedSpike`, `AgentSuccessRateLow`  
**SLO Impact**: SLO-04 Agent Success Rate (`>= 95%`), Zero-Trust Sandbox Security  
**Estimated MTTR**: 10–20 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Security Team / SRE Team

---

## 1. Symptoms
- [ ] Sandbox executions fail with `Connection timed out`, `Network is unreachable`, or `EgressDenied`
- [ ] `iptables` kernel/sandbox logs show `PVS_EGRESS_DROP` entries for legitimate endpoints (e.g., rotated IP for OpenAI/Anthropic/PyPI)
- [ ] `AgentSuccessRateLow` fires specifically on `build_agent` or code-execution tasks
- [ ] Security audit logs show repeated blocked egress attempts from sandbox containers

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect current network allowlist configuration
cat config/network_allowlist.yaml

# 2. Check recent sandbox egress block logs in application pods
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=150 \
  | grep -iE "egress|iptables|allowlist|PVS_EGRESS_DROP|dns"

# 3. Validate allowlist schema and DNS resolution locally
python -W error -m product_value_system doctor --repo-root .
```

### Decision Tree
- Blocked destination is an **unauthorized/suspicious external IP or C2 domain** -> Do **NOT** allowlist! Immediately invoke [Emergency Security Breach](emergency-security-breach.md)
- Legitimate approved domain rotated its upstream IP addresses (stale DNS cache in `iptables`) -> Section 3.A
- Newly required approved endpoint missing from `config/network_allowlist.yaml` -> Section 3.B

---

## 3. Remediation

### 3.A: Refresh Resolved Allowlist IPs (Zero-Downtime Reload)
When an approved upstream domain (e.g., `api.openai.com`, `api.anthropic.com`) rotates CDN/load-balancer IPs, trigger allowlist reload so `NetworkFilter` re-resolves DNS and refreshes `iptables` rules cleanly:
```bash
# Restart affected sandbox-host pods smoothly via rolling restart to re-resolve DNS allowlist
kubectl rollout restart deployment/product-value-system -n product-value-system
kubectl rollout status deployment/product-value-system -n product-value-system --timeout=5m
```
Verify legitimate sandbox calls succeed while unauthorized destinations remain blocked. **Rollback**: `kubectl rollout undo deployment/product-value-system -n product-value-system`.

### 3.B: Add Security-Approved Endpoint to Allowlist
1. Obtain explicit Security On-Call sign-off for the new domain/port in `#incidents`.
2. Add the minimal domain + port (`443`) entry to `config/network_allowlist.yaml` (never use wildcard `0.0.0.0/0` or disable `default_deny`).
3. Validate with `pytest tests/security/test_network_egress.py -v` and deploy via standard [Deployment Checklist](deployment-checklist.md).

---

## 4. Prevention
- [ ] Schedule periodic DNS refresh for `NetworkFilter` allowlist entries (`dns_refresh_interval_seconds`) so IP rotations never break egress
- [ ] Alert separately on expected-domain DNS misses vs unknown-destination `PVS_EGRESS_DROP` events
- [ ] Run `tests/security/test_network_egress.py` in CI on every change to `config/network_allowlist.yaml`

---

## 5. Escalation
If `PVS_EGRESS_DROP` logs indicate exfiltration attempts to unapproved domains or metadata endpoints (`169.254.169.254`), immediately page Security On-Call and follow [Emergency Security Breach](emergency-security-breach.md).

---

## 6. References
- [Sandbox Security Guide](../security/sandbox-security.md)
- [Network Filtering Architecture](../network-filtering.md)
- [Emergency Security Breach](emergency-security-breach.md)
- [Agent Success Rate Drop](incident-agent-success-drop.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Run `pytest tests/security/test_network_egress.py -v` to confirm default-deny blocks non-allowlisted destinations while approved endpoints pass after allowlist reload.

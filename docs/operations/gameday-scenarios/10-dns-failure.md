# GameDay Scenario: DNS Resolution Failure

**Scenario ID**: `GAMEDAY-023`  
**Category**: Network Resilience  
**Difficulty**: Medium  
**Duration**: 60 minutes  
**Prerequisites**: Chaos Mesh `DNSChaos` installed in staging, `NetworkFilter` allowlist configured

---

## Objective
Verify graceful degradation and clear error telemetry when cluster DNS (`CoreDNS`) or external domain resolution fails temporarily for sandbox egress or upstream provider endpoints.

**Success Criteria**:
- Health endpoints (`/health/live`) remain responsive while DNS degrades
- Logs emit explicit DNS resolution failure diagnostics (zero silent hangs)
- Service recovers cleanly without pod restarts once DNS returns

---

## Pre-GameDay Checklist
- [ ] Verify `CoreDNS` healthy in `kube-system`
- [ ] Review [Sandbox Egress Block Runbook](../../runbooks/incident-sandbox-egress-block.md)

---

## Procedure

### Phase 1: Baseline (5 min)
Verify DNS lookup latency and `/health/ready` `200 OK` in staging.

### Phase 2: Chaos Injection (5 min)
```bash
kubectl apply -f - <<'EOF'
apiVersion: chaos-mesh.org/v1alpha1
kind: DNSChaos
metadata:
  name: pvs-gameday-dns-error
  namespace: product-value-system-staging
spec:
  action: error
  mode: one
  patterns:
    - "api.openai.com"
  selector:
    namespaces:
      - product-value-system-staging
    labelSelectors:
      app.kubernetes.io/name: product-value-system
  duration: "3m"
EOF
```

### Phase 3: Observation (20 min)
Verify `ModelRouter` fails over to secondary provider or raises structured error, and inspect `NetworkFilter` logs.

### Phase 4: Recovery (10 min)
```bash
kubectl delete dnschaos pvs-gameday-dns-error -n product-value-system-staging --ignore-not-found
```
Confirm normal resolution resumes within `60s`.

### Phase 5: Retrospective (20 min)
Assess DNS cache TTLs and whether secondary LLM failover triggered cleanly.

---

## Cleanup
```bash
kubectl delete dnschaos pvs-gameday-dns-error -n product-value-system-staging --ignore-not-found
```

---

## Expected Outcomes
- **Green**: Structured failover/error within timeout; automatic recovery once DNS clears.
- **Yellow**: Cached negative DNS entry delayed recovery `> 2 min`.
- **Red**: Hanging sockets blocked worker event loop or crashed pods.

## Failure Mode Analysis
Inspect DNS timeout settings, `ndots` config, and `NetworkFilter` DNS refresh interval.

## Related Scenarios
- [GAMEDAY-041: OpenAI API Outage](14-openai-api-outage.md) · [Sandbox Egress Block Runbook](../../runbooks/incident-sandbox-egress-block.md)

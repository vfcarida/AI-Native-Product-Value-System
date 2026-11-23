# Runbook: HashiCorp Vault Sealed or Unreachable

**Severity**: P1 (Critical — blocks secret rotation, new pod startup, and dynamic DB leases)  
**Alert**: `VaultSealed`, `VaultUnreachable`, `VaultLeaseRenewalFailed`  
**SLO Impact**: SLO-01 Availability (`99.9%`), Security & Secret Management  
**Estimated MTTR**: 10–20 minutes  
**Last Updated**: 2026-11-23  
**Owner**: Security Team / SRE Team

---

## 1. Symptoms
- [ ] `vault status` reports `Sealed: true` or connection refused
- [ ] Newly starting pods fail readiness/startup with `VaultClientError` or `503 Vault is sealed`
- [ ] Dynamic PostgreSQL credential lease renewals fail in application logs
- [ ] Existing pods continue serving only until current credential leases expire

---

## 2. Diagnosis

### Quick Checks (2 minutes)
```bash
# 1. Inspect Vault pod state and seal status
kubectl get pods -n vault
kubectl exec -n vault vault-0 -- vault status

# 2. Check Vault server logs for auto-unseal KMS / storage backend errors
kubectl logs -n vault vault-0 --tail=100

# 3. Check PVS pods for Vault token/lease renewal warnings
kubectl logs -n product-value-system -l app.kubernetes.io/name=product-value-system --tail=100 \
  | grep -iE "vault|lease|seal|secret"
```

### Decision Tree
- Auto-unseal Cloud KMS IAM/network glitch -> Section 3.A
- Manual Shamir unseal required (quorum of unseal key custodians) -> Section 3.B
- Vault unsealed, but PVS Kubernetes auth token expired/misconfigured -> Section 3.C
- Suspected unauthorized tamper or forced seal by security team -> [Emergency Security Breach](emergency-security-breach.md)

---

## 3. Remediation

### 3.A: Restore Auto-Unseal KMS Connectivity
Verify AWS KMS / GCP KMS endpoint reachability and node IAM role permissions; once restored, restart the sealed Vault standby/active pod cleanly so auto-unseal executes automatically:
```bash
kubectl rollout status statefulset/vault -n vault --timeout=3m
kubectl exec -n vault vault-0 -- vault status
```
Verify `Sealed: false` and `HA Mode: active`. **Rollback**: N/A (read-only status verification).

### 3.B: Perform Quorum Shamir Unseal (When Auto-Unseal Unavailable)
Coordinate with **3 of 5** authorized key custodians via secure out-of-band bridge (never paste unseal keys in Slack or tickets). Each custodian runs interactively:
```bash
kubectl exec -it -n vault vault-0 -- vault operator unseal
```
Once `Unseal Progress` reaches threshold (`Sealed: false`), verify PVS pods renew secrets cleanly:
```bash
./scripts/runbook-automation/restart-unhealthy-pods.sh
```
**Rollback**: To re-seal immediately during an active compromise, run `vault operator seal`.

### 3.C: Refresh PVS Vault Auth & Verify Secret Access
```bash
# Validate Kubernetes auth role and policy bindings without printing secret values
kubectl exec -n vault vault-0 -- vault read auth/kubernetes/role/product-value-system

# Verify PVS health endpoint reports healthy configuration
kubectl exec -n product-value-system deploy/product-value-system -- \
  curl -fsS http://localhost:8000/health/ready
```

---

## 4. Prevention
- [ ] Run Vault in 3-node Raft HA mode with Cloud KMS auto-unseal enabled
- [ ] Monitor `vault_core_unsealed == 0` and lease renewal errors with P1 PagerDuty alerts
- [ ] Rehearse quarterly unseal & emergency secret revocation drills ([Vault Integration Guide](../security/vault-integration.md))

---

## 5. Escalation
Page Security On-Call immediately if Vault sealed unexpectedly (potential intrusion or KMS policy change) or if fewer than 3 key custodians are reachable within 10 minutes.

---

## 6. References
- [HashiCorp Vault Integration Guide](../security/vault-integration.md)
- [Emergency Secret Revocation](../operations/runbooks/emergency-secret-revocation.md)
- [Emergency Security Breach](emergency-security-breach.md)
- [Pod CrashLoopBackOff](incident-pod-crashloop.md)

---

## 7. Testing
**Last Tested**: 2026-11-23  
**Simulation**: Seal staging Vault instance, verify existing pods continue serving via cached leases while `VaultSealed` alert triggers, unseal Vault, and confirm automatic lease renewal resumes cleanly.

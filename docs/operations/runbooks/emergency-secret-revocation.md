# Emergency Secret Revocation Runbook

## Trigger
API key, database credential, or signing key confirmed or suspected compromised (detected by secret scanner, Vault audit anomaly, provider abuse alert, or security report).

## Severity & Target SLAs
- **Severity**: **P0 — Critical** (immediate incident response)
- **Detection to Revocation**: `< 10 minutes`
- **Detection to Full Recovery**: `< 20 minutes`

## Response Team
- **Incident Commander**: On-call SRE / Platform Engineer
- **Security Lead**: Security Engineering On-Call
- **Application Owner**: Product Value System Maintainer

---

## Step-by-Step Procedure

### 1. Confirm Scope & Containment (`0 – 5 min`)
- Review Vault audit logs (`vault/logs/audit.log`) for unexpected identity, IP, or bulk read bursts.
- Inspect OpenTelemetry/application logs for unauthorized model calls or spend spikes.
- If active exfiltration is ongoing, immediately trigger `EMERGENCY_STOP=1` on the control plane.

### 2. Revoke Compromised Credential & Issue Replacement (`5 – 10 min`)
- **Provider API keys**:
  - Revoke compromised key in provider console (OpenAI / Anthropic / Google Cloud / Azure / AWS IAM).
  - Generate replacement credential scoped to minimum required permissions.
  - Write new credential to Vault KV v2:
    ```bash
    vault kv put secret/product-value-system/openai api_key="<new-replacement-key>"
    ```
- **Dynamic PostgreSQL credentials**:
  - Immediately revoke the active lease (or all leases under the prefix):
    ```bash
    vault lease revoke -prefix database/creds/product-value-system-rw
    ```

### 3. Rolling Application Refresh (`10 – 15 min`)
- Perform zero-downtime rolling restart so all pods reload the updated secret and drop cached values:
  ```bash
  kubectl rollout restart deployment product-value-system -n product-value-system
  kubectl rollout status deployment product-value-system -n product-value-system
  ```
- In-process cache invalidation (if running standalone Python workers): `get_vault_client().clear_cache()`.

### 4. Verification (`15 – 20 min`)
- Verify Vault KV v2 version incremented: `vault kv metadata get secret/product-value-system/openai`.
- Verify `/health/ready`, `/slos`, and Grafana SLO dashboard show zero 401/403 authentication errors.
- Confirm the revoked credential is rejected by the external provider.

### 5. Post-Incident (`20+ min`)
- Record timeline, root cause, and revoked credential metadata (never raw key material) in the incident tracker.
- Publish blameless postmortem within **48 hours** and tighten Vault policies / pre-commit scanning rules if a gap was uncovered.

---

See also: [HashiCorp Vault Integration Guide](../../security/vault-integration.md) · [Production SLOs](../slos.md)

# HashiCorp Vault Integration Guide

## Overview

In production deployments, the AI-Native Product Value System retrieves runtime credentials exclusively from **HashiCorp Vault** via [`VaultClient`](../../src/product_value_system/secrets/vault_client.py), eliminating plaintext credentials from environment variables, config files, and Git repositories.

---

## Architecture

- **Static Secrets (KV v2)**: Provider API keys (OpenAI, Anthropic, Gemini, Azure OpenAI, AWS) and encryption keys stored under `secret/data/product-value-system/*` with versioned rollback and 30-day rotation cadence.
- **Dynamic Database Credentials (Database Engine)**: Short-lived PostgreSQL credentials (`1h` default TTL, `24h` max TTL) generated per instance via `PostgresDatabase.from_vault()` with automatic background lease renewal (`start_lease_renewal()`) and clean teardown (`close()`).
- **Authentication Modes**:
  - **Production (Kubernetes)**: Mounted ServiceAccount JWT (`/var/run/secrets/kubernetes.io/serviceaccount/token`) bound to a dedicated least-privilege Vault role.
  - **CI/CD**: Vault `AppRole` (`VAULT_ROLE_ID` + `VAULT_SECRET_ID`).
  - **Local Development**: `VAULT_TOKEN` or controlled environment fallback (`ENVIRONMENT=development`).
- **Kubernetes Vault Agent Injector**: Enabled via Helm `--set vault.enabled=true` to inject sidecar secret mounts (`/vault/secrets/*`) and `VAULT_ADDR` / `VAULT_AUTH_METHOD` / `VAULT_ROLE`.

---

## Secret Paths

| Secret | Vault KV v2 Path | Keys |
|---|---|---|
| OpenAI API Key | `secret/product-value-system/openai` | `api_key` |
| Anthropic API Key | `secret/product-value-system/anthropic` | `api_key` |
| Google Gemini API Key | `secret/product-value-system/gemini` | `api_key` |
| Azure OpenAI | `secret/product-value-system/azure_openai` | `api_key`, `endpoint`, `deployment` |
| AWS Credentials | `secret/product-value-system/aws` | `access_key_id`, `secret_access_key`, `region` |
| PostgreSQL Metadata | `secret/product-value-system/database` | `host`, `port`, `name` |
| Dynamic DB Creds | `database/creds/product-value-system-rw` | `username`, `password` (1h TTL) |
| Encryption Key | `secret/product-value-system/encryption` | `key` |

---

## Setup & Bootstrap

### 1. Install Optional `hvac` Dependency

```bash
pip install -e ".[vault]"
```

### 2. Apply Least-Privilege Policy & Kubernetes Auth

Policy file: [`deploy/vault/policies/product-value-system-policy.hcl`](../../deploy/vault/policies/product-value-system-policy.hcl).

```bash
vault secrets enable -path=secret kv-v2
vault policy write product-value-system-policy \
  deploy/vault/policies/product-value-system-policy.hcl

vault auth enable kubernetes
vault write auth/kubernetes/role/product-value-system \
  bound_service_account_names=product-value-system \
  bound_service_account_namespaces=product-value-system \
  policies=product-value-system-policy \
  ttl=1h
```

### 3. Configure Dynamic PostgreSQL Credentials

```bash
vault secrets enable database
vault write database/config/postgresql-primary \
  plugin_name=postgresql-database-plugin \
  connection_url="postgresql://{{username}}:{{password}}@db.internal:5432/product_value_system" \
  allowed_roles="product-value-system-ro,product-value-system-rw"

vault write database/roles/product-value-system-rw \
  db_name=postgresql-primary \
  creation_statements="CREATE ROLE \"{{name}}\" WITH LOGIN PASSWORD '{{password}}' VALID UNTIL '{{expiration}}' IN ROLE readwrite;" \
  default_ttl="1h" max_ttl="24h"
```

### 4. Migrate Existing `.env` Secrets

```bash
# Dry-run first to verify secret mappings without writing
bash scripts/migrate-secrets-to-vault.sh --dry-run --env-file .env

# Execute live migration
export VAULT_ADDR="https://vault.company.internal:8200"
export VAULT_TOKEN="<migration-token>"
bash scripts/migrate-secrets-to-vault.sh
```

### 5. Deploy on Kubernetes with Vault Agent Sidecar

```bash
helm upgrade --install pvs deploy/helm/product-value-system \
  --namespace product-value-system --create-namespace \
  --set vault.enabled=true \
  --set vault.address="https://vault.vault.svc.cluster.local:8200"
```

---

## Python SDK Usage

```python
from product_value_system.persistence.postgres import PostgresDatabase
from product_value_system.secrets import get_vault_client

vault = get_vault_client()
api_key = vault.get_secret("product-value-system/openai", "api_key")

# Dynamic short-lived PostgreSQL credentials with automatic lease renewal
db = PostgresDatabase.from_vault(role="product-value-system-rw", auto_renew=True)
```

All LLM adapters (`OpenAIModelAdapter`, `AnthropicAdapter`, `GeminiModelAdapter`, `AzureOpenAIModelAdapter`) resolve credentials automatically via `resolve_secret()` (explicit arg → Vault when configured → environment variable fallback → `/vault/secrets/*` sidecar file).

---

## Audit Logging, Rotation & Monitoring

- **Audit Device**: Enable Vault file/syslog audit logging (`vault audit enable file file_path=/vault/logs/audit.log`, >=90-day retention).
- **Telemetry**: `VaultClient` emits OpenTelemetry spans (`vault.secret.read`, `vault.secret.write`, `vault.database.credentials`) without ever logging secret values.
- **Rotation**: Rotate static provider keys every 30 days via `vault kv put` + rolling pod restart; dynamic DB credentials rotate automatically every 1 hour.
- **Emergency Revocation**: Follow the P0 runbook in [`docs/operations/runbooks/emergency-secret-revocation.md`](../operations/runbooks/emergency-secret-revocation.md) (target `< 10 min` revocation, `< 20 min` full recovery).
- **Prometheus Alerts**: Monitor `vault_core_handle_request_count`, `vault_database_credentials_requests`, and failed authentication rates.

---

## Troubleshooting

1. **Secret not found**: Verify `vault kv get secret/product-value-system/openai` and `vault policy read product-value-system-policy`.
2. **Kubernetes auth rejected**: Confirm `automountServiceAccountToken: true` on the pod and matching service account name/namespace in `auth/kubernetes/role/product-value-system`.
3. **Stale cached value after rotation**: Call `get_vault_client().clear_cache()` or trigger `kubectl rollout restart deployment/pvs`.
4. **Local development without Vault**: Keep `VAULT_ADDR` unset (or set `ENVIRONMENT=development`) to use standard environment variables seamlessly.

---

See also: [Emergency Secret Revocation Runbook](../operations/runbooks/emergency-secret-revocation.md) · [Secret Detection (Presidio ML)](secret-detection.md) · [Secret Management](../SECRET_MANAGEMENT.md)

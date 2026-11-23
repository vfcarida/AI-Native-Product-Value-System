# HashiCorp Vault Least-Privilege Policy for product-value-system (PROD-004)
# Apply with:
#   vault policy write product-value-system-policy deploy/vault/policies/product-value-system-policy.hcl

# Read-only access to application KV v2 secrets
path "secret/data/product-value-system/*" {
  capabilities = ["read", "list"]
}

path "secret/metadata/product-value-system/*" {
  capabilities = ["list", "read"]
}

# Generate short-lived dynamic PostgreSQL credentials
path "database/creds/product-value-system-ro" {
  capabilities = ["read"]
}

path "database/creds/product-value-system-rw" {
  capabilities = ["read"]
}

# Renew and revoke own dynamic secret leases
path "sys/leases/renew" {
  capabilities = ["update"]
}

path "sys/leases/revoke" {
  capabilities = ["update"]
}

# Allow application token self-renewal and lookup
path "auth/token/renew-self" {
  capabilities = ["update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}

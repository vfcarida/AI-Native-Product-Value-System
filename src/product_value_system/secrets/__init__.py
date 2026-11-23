"""
Enterprise secrets management backed by HashiCorp Vault (PROD-004).
"""

from product_value_system.secrets.vault_client import (
    VaultClient,
    VaultClientError,
    get_vault_client,
    reset_vault_client,
    resolve_secret,
)

__all__ = [
    "VaultClient",
    "VaultClientError",
    "get_vault_client",
    "reset_vault_client",
    "resolve_secret",
]

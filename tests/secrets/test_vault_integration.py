"""
Comprehensive unit & integration test suite for HashiCorp Vault enterprise secrets
management (PROD-004).

Covers:
- VaultClient initialization, Kubernetes JWT, AppRole, and Token authentication
- KV v2 read (key & full dict), write, soft-delete, TTL-enforced caching & invalidation
- Controlled environment-variable fallback (dev/test) vs strict fail-closed (prod)
- Dynamic PostgreSQL credentials, lease renewal, revocation, and background renewal lifecycle
- Adapter integration (OpenAI, Anthropic, Gemini, Azure OpenAI) & PostgresDatabase.from_vault()
- Vault HCL policy, Helm Vault sidecar values/schema, migration script, docs & runbook
- Cached retrieval latency (< 50ms target; sub-ms expected)
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import jsonschema
import pytest
import yaml

from product_value_system.adapters import (
    AnthropicAdapter,
    AzureOpenAIModelAdapter,
    GeminiModelAdapter,
    OpenAIAdapter,
)
from product_value_system.persistence.postgres import PostgresDatabase
from product_value_system.secrets import (
    VaultClient,
    VaultClientError,
    get_vault_client,
    reset_vault_client,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_HCL = REPO_ROOT / "deploy" / "vault" / "policies" / "product-value-system-policy.hcl"
MIGRATION_SCRIPT = REPO_ROOT / "scripts" / "migrate-secrets-to-vault.sh"
VAULT_DOC = REPO_ROOT / "docs" / "security" / "vault-integration.md"
REVOCATION_RUNBOOK = (
    REPO_ROOT / "docs" / "operations" / "runbooks" / "emergency-secret-revocation.md"
)
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "product-value-system"


@pytest.fixture(autouse=True)
def _reset_singleton() -> Iterator[None]:
    reset_vault_client()
    yield
    reset_vault_client()


@pytest.fixture
def mock_hvac_client() -> Iterator[MagicMock]:
    """Patch hvac.Client with a deterministic KV v2 + Database + Auth mock."""
    with patch("hvac.Client") as mock_cls:
        instance = MagicMock()
        mock_cls.return_value = instance
        instance.is_authenticated.return_value = True
        instance.secrets.kv.v2.read_secret_version.return_value = {
            "data": {
                "data": {
                    "api_key": "sk-vault-synthetic-key",
                    "host": "db.internal",
                    "port": 5432,
                    "name": "product_value_system",
                }
            }
        }
        instance.secrets.database.generate_credentials.return_value = {
            "data": {
                "username": "v-k8s-pvs-abc123",
                "password": "dynamic-ephemeral-password",
            },
            "lease_id": "database/creds/product-value-system-rw/lease-001",
            "lease_duration": 3600,
        }
        instance.sys.renew_lease.return_value = {
            "lease_id": "database/creds/product-value-system-rw/lease-001",
            "lease_duration": 3600,
        }
        yield instance


@pytest.mark.unit
def test_hvac_library_installed() -> None:
    """hvac >= 1.2.0 is installed in the environment."""
    import importlib.metadata

    import hvac

    assert hasattr(hvac, "Client")
    version = importlib.metadata.version("hvac")
    major_minor = tuple(int(p) for p in version.split(".")[:2])
    assert major_minor >= (1, 2), f"Expected hvac >= 1.2, got {version}"


@pytest.mark.unit
def test_vault_client_initialization_and_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VaultClient tracks explicit configuration cleanly without network calls."""
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    unconfigured = VaultClient(auth_method="token")
    assert unconfigured.is_configured is False
    assert unconfigured.mount_point == "secret"
    assert unconfigured.enable_cache is True
    assert unconfigured.cache_ttl == 300.0

    configured = VaultClient(vault_addr="https://vault.test:8200/", auth_method="token")
    assert configured.is_configured is True
    assert configured.vault_addr == "https://vault.test:8200"


@pytest.mark.unit
def test_get_secret_key_and_full_dict(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """VaultClient reads both a single key and a full secret dictionary from KV v2."""
    monkeypatch.setenv("VAULT_TOKEN", "dev-token-unit")
    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="token")

    api_key = client.get_secret("product-value-system/openai", "api_key")
    assert api_key == "sk-vault-synthetic-key"

    full = client.get_secret("product-value-system/database")
    assert full["host"] == "db.internal"
    assert full["port"] == 5432


@pytest.mark.unit
def test_secret_caching_ttl_expiration_and_invalidation(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory cache avoids repeat Vault calls, respects TTL, and invalidates on writes."""
    monkeypatch.setenv("VAULT_TOKEN", "dev-token-unit")
    client = VaultClient(
        vault_addr="https://vault.test:8200",
        auth_method="token",
        enable_cache=True,
        cache_ttl=60,
    )

    v1 = client.get_secret("product-value-system/openai", "api_key")
    v2 = client.get_secret("product-value-system/openai", "api_key")
    assert v1 == v2 == "sk-vault-synthetic-key"
    assert mock_hvac_client.secrets.kv.v2.read_secret_version.call_count == 1

    # Writing to the same path invalidates key-scoped cache entries
    client.set_secret("product-value-system/openai", {"api_key": "sk-vault-rotated"})
    _ = client.get_secret("product-value-system/openai", "api_key")
    assert mock_hvac_client.secrets.kv.v2.read_secret_version.call_count == 2

    # Force TTL expiration
    client._cache["product-value-system/openai:api_key"] = (
        time.monotonic() - 1.0,
        "stale",
    )
    _ = client.get_secret("product-value-system/openai", "api_key")
    assert mock_hvac_client.secrets.kv.v2.read_secret_version.call_count == 3

    client.clear_cache()
    assert client._cache == {}


@pytest.mark.unit
def test_fallback_to_env_on_error_vs_strict_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev fallback resolves env var on Vault error; strict production raises."""
    monkeypatch.setenv("VAULT_TOKEN", "invalid-token")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-fallback-value")

    with patch("hvac.Client") as mock_cls:
        mock_cls.return_value.is_authenticated.return_value = True
        mock_cls.return_value.secrets.kv.v2.read_secret_version.side_effect = RuntimeError(
            "Vault sealed"
        )

        dev_client = VaultClient(
            vault_addr="https://vault.test:8200",
            auth_method="token",
            fallback_to_env=True,
        )
        assert dev_client.get_secret("product-value-system/openai", "api_key") == (
            "sk-env-fallback-value"
        )

        prod_client = VaultClient(
            vault_addr="https://vault.test:8200",
            auth_method="token",
            fallback_to_env=False,
        )
        with pytest.raises(RuntimeError, match="Vault sealed"):
            prod_client.get_secret("product-value-system/openai", "api_key")


@pytest.mark.unit
def test_kubernetes_auth_reads_service_account_jwt(
    mock_hvac_client: MagicMock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kubernetes auth reads mounted ServiceAccount JWT and authenticates with Vault role."""
    jwt_file = tmp_path / "token"
    jwt_file.write_text("eyJhbGciOiJSUzI1NiJ9.synthetic-k8s-jwt\n", encoding="utf-8")
    monkeypatch.setenv("VAULT_K8S_TOKEN_PATH", str(jwt_file))
    monkeypatch.setenv("VAULT_ROLE", "product-value-system")

    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="kubernetes")
    client._authenticate()

    mock_hvac_client.auth.kubernetes.login.assert_called_once_with(
        role="product-value-system",
        jwt="eyJhbGciOiJSUzI1NiJ9.synthetic-k8s-jwt",
        mount_point="kubernetes",
    )


@pytest.mark.unit
def test_kubernetes_auth_fails_when_token_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Kubernetes JWT raises VaultClientError when fallback_to_env is False."""
    monkeypatch.setenv("VAULT_K8S_TOKEN_PATH", str(tmp_path / "missing-token"))
    client = VaultClient(
        vault_addr="https://vault.test:8200",
        auth_method="kubernetes",
        fallback_to_env=False,
    )
    with patch("hvac.Client"):
        with pytest.raises(VaultClientError, match="token not found"):
            client._authenticate()


@pytest.mark.unit
def test_approle_authentication(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AppRole authentication submits VAULT_ROLE_ID and VAULT_SECRET_ID."""
    monkeypatch.setenv("VAULT_ROLE_ID", "role-id-123")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret-id-456")
    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="approle")
    client._authenticate()

    mock_hvac_client.auth.approle.login.assert_called_once_with(
        role_id="role-id-123",
        secret_id="secret-id-456",
    )


@pytest.mark.unit
def test_unknown_auth_method_rejected() -> None:
    """Unknown auth_method raises ValueError."""
    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="kerberos")
    with patch("hvac.Client"):
        with pytest.raises(ValueError, match="Unknown Vault auth method"):
            client._authenticate()


@pytest.mark.unit
def test_set_and_delete_secret_kv_v2(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set_secret and delete_secret call KV v2 APIs cleanly."""
    monkeypatch.setenv("VAULT_TOKEN", "dev-token-unit")
    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="token")

    client.set_secret("product-value-system/openai", {"api_key": "sk-new"})
    mock_hvac_client.secrets.kv.v2.create_or_update_secret.assert_called_once_with(
        path="product-value-system/openai",
        secret={"api_key": "sk-new"},
        mount_point="secret",
    )

    client.delete_secret("product-value-system/openai")
    mock_hvac_client.secrets.kv.v2.delete_latest_version_of_secret.assert_called_once_with(
        path="product-value-system/openai",
        mount_point="secret",
    )


@pytest.mark.unit
def test_dynamic_database_credentials_renewal_and_revocation(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dynamic DB credentials generate short-lived creds, renew leases, and revoke cleanly."""
    monkeypatch.setenv("VAULT_TOKEN", "dev-token-unit")
    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="token")

    creds = client.get_database_credentials(role="product-value-system-rw")
    assert creds["username"] == "v-k8s-pvs-abc123"
    assert creds["password"] == "dynamic-ephemeral-password"
    assert creds["lease_duration"] == 3600

    client.renew_lease(creds["lease_id"], increment=1800)
    mock_hvac_client.sys.renew_lease.assert_called_once_with(
        lease_id=creds["lease_id"],
        increment=1800,
    )

    client.start_lease_renewal(creds["lease_id"], lease_duration=3600)
    client.stop_lease_renewals(revoke=True)
    mock_hvac_client.sys.revoke_lease.assert_called_once_with(lease_id=creds["lease_id"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_database_from_vault_builds_dsn_and_stops_renewal(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PostgresDatabase.from_vault constructs escaped DSN and stops lease renewal on close()."""
    monkeypatch.setenv("VAULT_TOKEN", "dev-token-unit")
    vault = VaultClient(vault_addr="https://vault.test:8200", auth_method="token")

    db = PostgresDatabase.from_vault(
        role="product-value-system-rw",
        vault_client=vault,
        auto_renew=True,
    )
    assert db.dsn.startswith("postgresql://v-k8s-pvs-abc123:")
    assert "@db.internal:5432/product_value_system" in db.dsn
    assert db._vault_lease_id == "database/creds/product-value-system-rw/lease-001"

    await db.close()
    assert db._vault_client is None
    assert vault._renewal_threads == {}


@pytest.mark.unit
def test_all_llm_adapters_resolve_api_keys_from_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenAI, Anthropic, Gemini, and Azure OpenAI adapters fetch keys from Vault."""
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "AZURE_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)

    mock_vault = Mock()
    mock_vault.is_configured = True

    def _get_secret(path: str, key: str | None = None) -> str:
        mapping = {
            "product-value-system/openai": "sk-vault-openai-key",
            "product-value-system/anthropic": "sk-ant-vault-anthropic-key",
            "product-value-system/gemini": "vault-gemini-key",
            "product-value-system/azure_openai": "vault-azure-key",
        }
        return mapping[path]

    mock_vault.get_secret.side_effect = _get_secret

    with patch("product_value_system.secrets.get_vault_client", return_value=mock_vault):
        openai_ad = OpenAIAdapter()
        anthropic_ad = AnthropicAdapter()
        gemini_ad = GeminiModelAdapter()
        azure_ad = AzureOpenAIModelAdapter(
            endpoint="https://myorg.openai.azure.com",
            deployment="gpt-4o",
        )

    assert openai_ad.api_key == "sk-vault-openai-key"
    assert anthropic_ad.api_key == "sk-ant-vault-anthropic-key"
    assert gemini_ad.api_key == "vault-gemini-key"
    assert azure_ad.api_key == "vault-azure-key"


@pytest.mark.unit
def test_singleton_get_vault_client() -> None:
    """get_vault_client returns the same process-wide singleton instance."""
    c1 = get_vault_client()
    c2 = get_vault_client()
    assert c1 is c2


@pytest.mark.unit
def test_vault_policy_hcl_and_migration_script_and_docs_exist() -> None:
    """Least-privilege HCL policy, migration script, Vault guide & emergency runbook exist."""
    assert POLICY_HCL.exists()
    hcl = POLICY_HCL.read_text(encoding="utf-8")
    assert 'path "secret/data/product-value-system/*"' in hcl
    assert 'path "database/creds/product-value-system-ro"' in hcl
    assert 'path "database/creds/product-value-system-rw"' in hcl
    assert 'path "sys/leases/renew"' in hcl
    assert 'capabilities = ["read", "list"]' in hcl

    assert MIGRATION_SCRIPT.exists()
    script_text = MIGRATION_SCRIPT.read_text(encoding="utf-8")
    assert "--dry-run" in script_text
    assert "vault kv put" in script_text

    assert VAULT_DOC.exists()
    assert "KV v2" in VAULT_DOC.read_text(encoding="utf-8")

    assert REVOCATION_RUNBOOK.exists()
    runbook_text = REVOCATION_RUNBOOK.read_text(encoding="utf-8")
    assert "P0" in runbook_text
    assert "kubectl rollout restart" in runbook_text


@pytest.mark.unit
def test_helm_vault_values_and_deployment_sidecar_annotations() -> None:
    """Helm values.yaml vault config conforms to values.schema.json and deployment annotations."""
    values = yaml.safe_load((CHART_DIR / "values.yaml").read_text(encoding="utf-8"))
    schema = json.loads((CHART_DIR / "values.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(instance=values, schema=schema)

    assert values["vault"]["authMethod"] == "kubernetes"
    assert values["vault"]["role"] == "product-value-system"

    deployment_tmpl = (CHART_DIR / "templates" / "deployment.yaml").read_text(encoding="utf-8")
    assert "vault.hashicorp.com/agent-inject:" in deployment_tmpl
    assert "VAULT_ADDR" in deployment_tmpl
    assert "VAULT_AUTH_METHOD" in deployment_tmpl


@pytest.mark.unit
def test_cached_secret_retrieval_latency_under_50ms(
    mock_hvac_client: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cached secret retrieval adds < 50ms latency (expected sub-ms)."""
    monkeypatch.setenv("VAULT_TOKEN", "dev-token-unit")
    client = VaultClient(vault_addr="https://vault.test:8200", auth_method="token")
    assert client.get_secret("product-value-system/openai", "api_key") == "sk-vault-synthetic-key"

    samples_ms: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        val = client.get_secret("product-value-system/openai", "api_key")
        samples_ms.append((time.perf_counter() - t0) * 1000.0)
        assert val == "sk-vault-synthetic-key"

    p50_ms = statistics.median(samples_ms)
    assert p50_ms < 50.0, f"Cached secret retrieval p50 {p50_ms:.2f}ms exceeded 50ms target"
    assert mock_hvac_client.secrets.kv.v2.read_secret_version.call_count == 1


@pytest.mark.integration_real
def test_vault_integration_real_cluster() -> None:
    """Live Vault cluster read/write/delete integration test (requires VAULT_ADDR + TOKEN)."""
    if not os.getenv("VAULT_ADDR") or not os.getenv("VAULT_TOKEN"):
        pytest.skip("VAULT_ADDR / VAULT_TOKEN not set; skipping live Vault integration test")

    client: Any = VaultClient(auth_method="token", fallback_to_env=False)
    test_path = "product-value-system/integration-probe"
    client.set_secret(test_path, {"probe_key": "probe_value"})
    try:
        assert client.get_secret(test_path, "probe_key") == "probe_value"
    finally:
        client.delete_secret(test_path)

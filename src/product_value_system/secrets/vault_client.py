"""
HashiCorp Vault client for enterprise secrets management (PROD-004).

Provides:
- Thread-safe ``VaultClient`` supporting Kubernetes JWT, AppRole, and Token auth
- KV v2 secret reads, writes, soft-deletes with monotonic TTL caching
- Dynamic PostgreSQL credential generation, lease renewal, background auto-renewal,
  and emergency lease revocation
- Controlled environment-variable fallback for local development and tests
- OpenTelemetry spans and audit logs that NEVER emit raw secret values
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Sequence
from contextlib import nullcontext
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_VAULT_ADDR = "https://vault.company.internal:8200"
_K8S_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"

try:
    from opentelemetry import trace as _otel_trace

    _TRACER: Any | None = _otel_trace.get_tracer(__name__)
except Exception:
    _TRACER = None


class VaultClientError(RuntimeError):
    """Raised when HashiCorp Vault authentication or secret operations fail."""


class VaultClient:
    """Thread-safe HashiCorp Vault client with TTL caching, dynamic DB creds, and fallback."""

    def __init__(
        self,
        vault_addr: str | None = None,
        auth_method: str = "kubernetes",
        mount_point: str = "secret",
        namespace: str | None = None,
        enable_cache: bool = True,
        cache_ttl: int = 300,
        fallback_to_env: bool = False,
        *,
        client: Any | None = None,
        verify: bool | str | None = None,
    ) -> None:
        env_addr = os.getenv("VAULT_ADDR")
        self._explicitly_configured: bool = bool(vault_addr or env_addr or client is not None)
        self.vault_addr: str = (vault_addr or env_addr or _DEFAULT_VAULT_ADDR).rstrip("/")
        self.auth_method: str = auth_method.strip().lower()
        self.mount_point: str = mount_point
        self.namespace: str | None = namespace or os.getenv("VAULT_NAMESPACE") or None
        self.enable_cache: bool = bool(enable_cache)
        self.cache_ttl: float = max(0.0, float(cache_ttl))
        self.fallback_to_env: bool = bool(fallback_to_env)

        if verify is not None:
            self.verify: bool | str = verify
        else:
            cacert = os.getenv("VAULT_CACERT")
            self.verify = cacert if cacert else True

        self._lock = threading.RLock()
        self._client: Any | None = client
        self._authenticated: bool = client is not None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._renewal_stops: dict[str, threading.Event] = {}
        self._renewal_threads: dict[str, threading.Thread] = {}

    @property
    def is_configured(self) -> bool:
        """Return True when ``VAULT_ADDR`` or an explicit client/address was configured."""
        return self._explicitly_configured

    def _get_client(self) -> Any:
        """Lazy-initialize and authenticate the underlying ``hvac.Client``."""
        with self._lock:
            if self._client is None:
                try:
                    import hvac
                except ImportError as exc:
                    raise VaultClientError(
                        "hvac library is required for HashiCorp Vault integration. "
                        "Install via `pip install 'product-value-system[vault]'`."
                    ) from exc

                self._client = hvac.Client(
                    url=self.vault_addr,
                    namespace=self.namespace,
                    verify=self.verify,
                )
            if not self._authenticated:
                self._authenticate()
            return self._client

    def _authenticate(self) -> None:
        """Authenticate against Vault using kubernetes, approle, or token auth."""
        with self._lock:
            if self._authenticated:
                return
            if self._client is None:
                try:
                    import hvac
                except ImportError as exc:
                    raise VaultClientError(
                        "hvac library not installed; cannot authenticate to Vault."
                    ) from exc
                self._client = hvac.Client(
                    url=self.vault_addr,
                    namespace=self.namespace,
                    verify=self.verify,
                )

            try:
                if self.auth_method == "kubernetes":
                    self._authenticate_kubernetes()
                elif self.auth_method == "approle":
                    self._authenticate_approle()
                elif self.auth_method == "token":
                    self._authenticate_token()
                else:
                    raise ValueError(
                        f"Unknown Vault auth method: {self.auth_method!r} "
                        "(expected 'kubernetes', 'approle', or 'token')"
                    )

                if not self._client.is_authenticated():
                    raise VaultClientError("Vault authentication failed (client not authenticated)")

                self._authenticated = True
                logger.info(
                    "Vault authentication successful (method=%s, addr=%s)",
                    self.auth_method,
                    self.vault_addr,
                )
            except Exception as exc:
                logger.error("Vault authentication failed (method=%s): %s", self.auth_method, exc)
                if not self.fallback_to_env:
                    raise
                logger.warning("Vault unavailable; falling back to environment variables")

    def _authenticate_kubernetes(self) -> None:
        """Authenticate using the mounted Kubernetes ServiceAccount JWT."""
        jwt_path = os.getenv("VAULT_K8S_TOKEN_PATH", _K8S_SA_TOKEN_PATH)
        if not os.path.exists(jwt_path):
            raise VaultClientError(f"Kubernetes service account token not found at {jwt_path}")

        with open(jwt_path, encoding="utf-8") as fh:
            jwt = fh.read().strip()

        role = os.getenv("VAULT_ROLE", "product-value-system")
        mount_point = os.getenv("VAULT_K8S_MOUNT_POINT", "kubernetes")
        assert self._client is not None
        self._client.auth.kubernetes.login(
            role=role,
            jwt=jwt,
            mount_point=mount_point,
        )

    def _authenticate_approle(self) -> None:
        """Authenticate using Vault AppRole (``VAULT_ROLE_ID`` + ``VAULT_SECRET_ID``)."""
        role_id = os.getenv("VAULT_ROLE_ID")
        secret_id = os.getenv("VAULT_SECRET_ID")
        if not role_id or not secret_id:
            raise VaultClientError(
                "VAULT_ROLE_ID and VAULT_SECRET_ID are required for AppRole auth"
            )
        assert self._client is not None
        self._client.auth.approle.login(role_id=role_id, secret_id=secret_id)

    def _authenticate_token(self) -> None:
        """Authenticate using a static ``VAULT_TOKEN`` (dev/test only)."""
        token = os.getenv("VAULT_TOKEN")
        if not token:
            raise VaultClientError("VAULT_TOKEN is required for token auth")
        assert self._client is not None
        self._client.token = token

    def get_secret(self, path: str, key: str | None = None) -> Any:
        """Retrieve a secret value (or full dict) from Vault KV v2 with TTL caching."""
        cache_key = f"{path}:{key}" if key else path
        now = time.monotonic()

        if self.enable_cache:
            with self._lock:
                entry = self._cache.get(cache_key)
                if entry is not None:
                    expires_at, cached_val = entry
                    if now < expires_at:
                        logger.debug("Vault cache hit for path=%s", path)
                        return cached_val
                    self._cache.pop(cache_key, None)

        span_cm = (
            _TRACER.start_as_current_span("vault.secret.read")
            if _TRACER is not None
            else nullcontext()
        )
        with span_cm as span:
            if span is not None:
                span.set_attribute("vault.mount_point", self.mount_point)
                span.set_attribute("vault.path", path)
                span.set_attribute("vault.has_key", key is not None)
            try:
                client = self._get_client()
                response = client.secrets.kv.v2.read_secret_version(
                    path=path,
                    mount_point=self.mount_point,
                )
                data = response["data"]["data"]
                result = data.get(key) if key is not None else dict(data)

                if self.enable_cache and self.cache_ttl > 0:
                    with self._lock:
                        self._cache[cache_key] = (time.monotonic() + self.cache_ttl, result)
                return result
            except Exception as exc:
                logger.error("Vault error reading path=%s: %s", path, exc)
                if self.fallback_to_env:
                    return self._get_from_env(path, key)
                raise

    @staticmethod
    def _get_from_env(path: str, key: str | None) -> str | None:
        """Resolve a dev/test environment-variable fallback from Vault path and key."""
        service = path.strip("/").split("/")[-1].replace("-", "_").upper()
        candidates: list[str] = []
        if key:
            norm_key = key.strip().replace("-", "_").upper()
            candidates.append(f"{service}_{norm_key}")
            if service == "DATABASE":
                candidates.append(f"POSTGRES_{norm_key}")
            if service == "GEMINI" and norm_key == "API_KEY":
                candidates.append("GOOGLE_API_KEY")
            candidates.append(norm_key)
        else:
            candidates.append(service)

        for env_name in dict.fromkeys(candidates):
            val = os.getenv(env_name)
            if val:
                logger.warning("Using environment variable fallback: %s", env_name)
                return val
        return None

    def _invalidate_path_cache(self, path: str) -> None:
        with self._lock:
            prefix = f"{path}:"
            stale = [k for k in self._cache if k == path or k.startswith(prefix)]
            for k in stale:
                self._cache.pop(k, None)

    def set_secret(self, path: str, data: dict[str, Any]) -> None:
        """Create or update a secret in Vault KV v2 and invalidate cached entries."""
        span_cm = (
            _TRACER.start_as_current_span("vault.secret.write")
            if _TRACER is not None
            else nullcontext()
        )
        with span_cm as span:
            if span is not None:
                span.set_attribute("vault.mount_point", self.mount_point)
                span.set_attribute("vault.path", path)
            try:
                client = self._get_client()
                client.secrets.kv.v2.create_or_update_secret(
                    path=path,
                    secret=data,
                    mount_point=self.mount_point,
                )
                if self.enable_cache:
                    self._invalidate_path_cache(path)
                logger.info("Vault secret written: path=%s (keys=%d)", path, len(data))
            except Exception as exc:
                logger.error("Failed to write Vault secret path=%s: %s", path, exc)
                raise

    def delete_secret(self, path: str) -> None:
        """Soft-delete the latest version of a secret in Vault KV v2."""
        try:
            client = self._get_client()
            client.secrets.kv.v2.delete_latest_version_of_secret(
                path=path,
                mount_point=self.mount_point,
            )
            if self.enable_cache:
                self._invalidate_path_cache(path)
            logger.info("Vault secret soft-deleted: path=%s", path)
        except Exception as exc:
            logger.error("Failed to delete Vault secret path=%s: %s", path, exc)
            raise

    def get_database_credentials(
        self,
        role: str = "product-value-system-ro",
        *,
        mount_point: str = "database",
    ) -> dict[str, Any]:
        """Request short-lived dynamic database credentials from the Vault database engine."""
        span_cm = (
            _TRACER.start_as_current_span("vault.database.credentials")
            if _TRACER is not None
            else nullcontext()
        )
        with span_cm as span:
            if span is not None:
                span.set_attribute("vault.database.role", role)
            try:
                client = self._get_client()
                response = client.secrets.database.generate_credentials(
                    name=role,
                    mount_point=mount_point,
                )
                return {
                    "username": str(response["data"]["username"]),
                    "password": str(response["data"]["password"]),
                    "lease_id": str(response["lease_id"]),
                    "lease_duration": int(response["lease_duration"]),
                }
            except Exception as exc:
                logger.error("Failed to obtain dynamic DB credentials (role=%s): %s", role, exc)
                raise

    def renew_lease(self, lease_id: str, increment: int = 3600) -> dict[str, Any]:
        """Renew an active Vault lease before expiration."""
        try:
            client = self._get_client()
            res = client.sys.renew_lease(lease_id=lease_id, increment=increment)
            logger.info("Vault lease renewed (increment=%ds)", increment)
            return dict(res) if isinstance(res, dict) else {"lease_id": lease_id}
        except Exception as exc:
            logger.error("Failed to renew Vault lease: %s", exc)
            raise

    def revoke_lease(self, lease_id: str) -> None:
        """Immediately revoke an active Vault lease (emergency revocation / clean shutdown)."""
        try:
            client = self._get_client()
            client.sys.revoke_lease(lease_id=lease_id)
            logger.info("Vault lease revoked")
        except Exception as exc:
            logger.error("Failed to revoke Vault lease: %s", exc)
            raise

    def start_lease_renewal(
        self,
        lease_id: str,
        lease_duration: int,
        *,
        renew_fraction: float = 0.75,
    ) -> threading.Event:
        """Start a background daemon thread renewing ``lease_id`` before TTL expiry."""
        stop_event = threading.Event()
        interval = max(1.0, float(lease_duration) * max(0.1, min(0.9, renew_fraction)))

        def _loop() -> None:
            while not stop_event.wait(interval):
                try:
                    self.renew_lease(lease_id, increment=lease_duration)
                except Exception as exc:
                    logger.warning("Background Vault lease renewal failed: %s", exc)

        thread = threading.Thread(
            target=_loop,
            name="pvs-vault-lease-renewer",
            daemon=True,
        )
        with self._lock:
            self._renewal_stops[lease_id] = stop_event
            self._renewal_threads[lease_id] = thread
        thread.start()
        return stop_event

    def stop_lease_renewals(self, *, revoke: bool = False) -> None:
        """Gracefully stop all background lease renewal loops (and optionally revoke leases)."""
        with self._lock:
            stops = list(self._renewal_stops.items())
            threads = list(self._renewal_threads.values())
            self._renewal_stops.clear()
            self._renewal_threads.clear()

        for lease_id, ev in stops:
            ev.set()
            if revoke:
                try:
                    self.revoke_lease(lease_id)
                except Exception as exc:
                    logger.debug("Best-effort lease revocation failed during shutdown: %s", exc)

        for thread in threads:
            thread.join(timeout=1.0)

    def clear_cache(self) -> None:
        """Clear all cached secrets from memory."""
        with self._lock:
            self._cache.clear()
        logger.debug("Vault secret cache cleared")


_vault_client: VaultClient | None = None
_vault_lock = threading.Lock()


def get_vault_client() -> VaultClient:
    """Return the process-wide singleton ``VaultClient`` (lazy initialized)."""
    global _vault_client
    with _vault_lock:
        if _vault_client is None:
            cache_ttl_raw = os.getenv("VAULT_CACHE_TTL", "300")
            try:
                cache_ttl = int(cache_ttl_raw)
            except ValueError:
                cache_ttl = 300
            _vault_client = VaultClient(
                vault_addr=os.getenv("VAULT_ADDR"),
                auth_method=os.getenv("VAULT_AUTH_METHOD", "kubernetes"),
                namespace=os.getenv("VAULT_NAMESPACE") or None,
                cache_ttl=cache_ttl,
                fallback_to_env=os.getenv("ENVIRONMENT", "production").lower() != "production",
            )
        return _vault_client


def reset_vault_client() -> None:
    """Reset the singleton ``VaultClient`` (primarily for test isolation)."""
    global _vault_client
    with _vault_lock:
        if _vault_client is not None:
            _vault_client.stop_lease_renewals()
            _vault_client.clear_cache()
        _vault_client = None


def resolve_secret(
    *,
    vault_path: str,
    key: str,
    env_vars: Sequence[str],
    explicit: str | None = None,
) -> str | None:
    """Resolve a credential from explicit arg -> Vault (when configured) -> environment variables.

    Avoids opening network sockets in local/CI environments when ``VAULT_ADDR`` is unset,
    while honoring patched ``get_vault_client()`` mocks and live Vault deployments.
    """
    if explicit:
        return explicit

    import product_value_system.secrets as secrets_pkg

    vault = secrets_pkg.get_vault_client()
    if getattr(vault, "is_configured", True):
        val = vault.get_secret(path=vault_path, key=key)
        if isinstance(val, str) and val:
            return val

    for var in env_vars:
        env_val = os.environ.get(var)
        if env_val:
            return env_val

    # Also support Kubernetes Vault Agent sidecar secret files if present
    agent_file = Path("/vault/secrets") / vault_path.split("/")[-1]
    if agent_file.is_file():
        for line in agent_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            for var in env_vars:
                prefix = f"export {var}="
                if stripped.startswith(prefix):
                    return stripped[len(prefix) :].strip().strip('"').strip("'")
    return None

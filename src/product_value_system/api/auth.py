"""
Enterprise authentication (OIDC/JWT + constant-time API keys), multi-tenant
context propagation, and Redis-backed distributed rate limiting (EVO-P3-02).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TenantContext:
    """Verified caller identity, tenant scope, and RBAC permissions."""

    tenant_id: str
    user_id: str
    role: str
    permissions: frozenset[str]
    auth_method: str = "api_key"

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


DEFAULT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(
        {
            "runs:read",
            "runs:write",
            "runs:approve",
            "agents:read",
            "audit:read",
            "gdpr:manage",
        }
    ),
    "approver": frozenset({"runs:read", "runs:write", "runs:approve", "agents:read"}),
    "developer": frozenset({"runs:read", "runs:write", "agents:read"}),
    "viewer": frozenset({"runs:read", "agents:read"}),
}


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode((segment + padding).encode("ascii"))


def create_hs256_jwt(
    claims: Mapping[str, Any],
    secret: str,
    *,
    expires_in_seconds: int = 3600,
) -> str:
    """Mint a standards-compliant HS256 JWT (for tests, local dev, and internal service tokens)."""
    now = int(time.time())
    payload: dict[str, Any] = dict(claims)
    payload.setdefault("iat", now)
    payload.setdefault("exp", now + expires_in_seconds)

    header_b64 = _b64url_encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def verify_hs256_jwt(
    token: str,
    secret: str,
    *,
    issuer: str | None = None,
    audience: str | None = None,
    leeway_seconds: int = 30,
) -> dict[str, Any]:
    """Verify an HS256 JWT in constant time (``alg``, ``exp``, ``nbf``, ``iss``, ``aud``)."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT structure")
    header_b64, payload_b64, sig_b64 = parts

    try:
        header = json.loads(_b64url_decode(header_b64).decode("utf-8"))
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
        provided_sig = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError("Invalid JWT encoding") from exc

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise ValueError("Unsupported JWT algorithm")
    if not isinstance(payload, dict):
        raise ValueError("Invalid JWT payload")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise ValueError("Invalid JWT signature")

    now = time.time()
    exp = payload.get("exp")
    if exp is None or not isinstance(exp, (int, float)):
        raise ValueError("JWT missing numeric exp claim")
    if now > float(exp) + leeway_seconds:
        raise ValueError("JWT token expired")

    nbf = payload.get("nbf")
    if nbf is not None and isinstance(nbf, (int, float)) and now < float(nbf) - leeway_seconds:
        raise ValueError("JWT token not yet valid")

    if issuer and payload.get("iss") != issuer:
        raise ValueError("JWT issuer mismatch")

    if audience:
        aud_claim = payload.get("aud")
        if isinstance(aud_claim, list):
            if audience not in aud_claim:
                raise ValueError("JWT audience mismatch")
        elif aud_claim != audience:
            raise ValueError("JWT audience mismatch")

    return payload


class JWKSKeyResolver:
    """
    OAuth 2.1 / OIDC Dynamic JWKS Key Resolver with TTL caching and automatic
    cache-miss key rotation (``kid`` lookup -> refresh on unknown ``kid``).
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        cache_ttl_seconds: float = 300.0,
        min_refresh_interval_seconds: float = 1.0,
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not jwks_url.strip():
            raise ValueError("jwks_url must be non-empty")
        self.jwks_url = jwks_url.strip()
        self.cache_ttl_seconds = max(1.0, float(cache_ttl_seconds))
        self.min_refresh_interval_seconds = max(0.0, float(min_refresh_interval_seconds))
        self._timeout = float(timeout)
        self._transport = transport
        self._lock = threading.RLock()
        self._keys_by_kid: dict[str, dict[str, Any]] = {}
        self._expires_at: float = 0.0
        self._last_refresh_at: float = 0.0
        self.refresh_count: int = 0

    def _fetch_jwks(self) -> dict[str, dict[str, Any]]:
        client_kwargs: dict[str, Any] = {"timeout": httpx.Timeout(self._timeout)}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        with httpx.Client(**client_kwargs) as client:
            resp = client.get(self.jwks_url)
            resp.raise_for_status()
            body = resp.json()
        keys_raw = body.get("keys")
        if not isinstance(keys_raw, list):
            raise ValueError("JWKS document missing 'keys' array")
        indexed: dict[str, dict[str, Any]] = {}
        for item in keys_raw:
            if isinstance(item, dict):
                kid = str(item.get("kid") or "").strip()
                if kid:
                    indexed[kid] = dict(item)
                elif len(keys_raw) == 1:
                    indexed["__default__"] = dict(item)
        return indexed

    def refresh(self, *, force: bool = False) -> dict[str, dict[str, Any]]:
        """Refresh JWK cache respecting TTL and anti-DoS minimum refresh interval."""
        with self._lock:
            now = time.monotonic()
            if not force and self._keys_by_kid and now < self._expires_at:
                return dict(self._keys_by_kid)
            if (
                self._last_refresh_at > 0.0
                and (now - self._last_refresh_at) < self.min_refresh_interval_seconds
                and self._keys_by_kid
            ):
                return dict(self._keys_by_kid)
            fetched = self._fetch_jwks()
            self._keys_by_kid = fetched
            self._last_refresh_at = now
            self._expires_at = now + self.cache_ttl_seconds
            self.refresh_count += 1
            return dict(self._keys_by_kid)

    def resolve_jwk(self, kid: str | None) -> dict[str, Any]:
        """Resolve JWK dict by ``kid``, performing one forced refresh on cache miss."""
        lookup = (kid or "").strip()
        keys = self.refresh(force=False)
        if lookup and lookup in keys:
            return keys[lookup]
        if not lookup and "__default__" in keys:
            return keys["__default__"]

        # Cache miss (possible IdP key rotation): force one refresh
        refreshed = self.refresh(force=True)
        if lookup and lookup in refreshed:
            return refreshed[lookup]
        if not lookup and "__default__" in refreshed:
            return refreshed["__default__"]
        raise ValueError(f"Unknown JWKS signing key id: {lookup or '<missing>'!r}")

    def verify_jwt(
        self,
        token: str,
        *,
        issuer: str | None = None,
        audience: str | None = None,
        allowed_algorithms: tuple[str, ...] = ("RS256", "ES256"),
    ) -> dict[str, Any]:
        """Verify an OIDC/OAuth 2.1 JWT against dynamically resolved JWKS keys."""
        try:
            import jwt as pyjwt
            from jwt import PyJWK
        except ImportError as exc:
            raise ValueError("PyJWT[crypto] required for JWKS OIDC verification") from exc

        header = pyjwt.get_unverified_header(token)
        alg = str(header.get("alg") or "")
        if alg not in allowed_algorithms:
            raise ValueError(f"Disallowed JWT algorithm: {alg!r}")
        kid = str(header.get("kid") or "").strip() or None

        jwk_dict = self.resolve_jwk(kid)
        signing_key = PyJWK.from_dict(jwk_dict).key
        decode_options: Any = {
            "require": ["exp"],
            "verify_aud": bool(audience),
            "verify_iss": bool(issuer),
        }
        decoded: dict[str, Any] = pyjwt.decode(
            token,
            signing_key,
            algorithms=[alg],
            issuer=issuer,
            audience=audience,
            options=decode_options,
        )
        return decoded


class ControlPlaneAuthenticator:
    """
    Authenticates incoming Control Plane HTTP requests via:
    1. OIDC/OAuth 2.1 JWT ``Authorization: Bearer <jwt>`` (dynamic JWKS, static pubkey, or HS256)
    2. Constant-time API keys mapped to ``TenantContext`` (``X-API-Key`` or ``Bearer``)
    """

    def __init__(
        self,
        *,
        api_keys: Mapping[str, TenantContext] | None = None,
        jwt_secret: str | None = None,
        jwt_public_key: str | None = None,
        jwt_issuer: str | None = None,
        jwt_audience: str | None = None,
        jwks_url: str | None = None,
        jwks_resolver: JWKSKeyResolver | None = None,
        jwks_cache_ttl_seconds: float = 300.0,
        allow_anonymous_dev: bool = False,
    ) -> None:
        self._api_keys: dict[str, TenantContext] = dict(api_keys or {})
        self.jwt_secret = jwt_secret or ""
        self.jwt_public_key = jwt_public_key or ""
        self.jwt_issuer = jwt_issuer
        self.jwt_audience = jwt_audience
        self.jwks_url = (jwks_url or "").strip()
        self._jwks_resolver: JWKSKeyResolver | None = jwks_resolver or (
            JWKSKeyResolver(self.jwks_url, cache_ttl_seconds=jwks_cache_ttl_seconds)
            if self.jwks_url
            else None
        )
        if self._jwks_resolver is not None and not self.jwks_url:
            self.jwks_url = self._jwks_resolver.jwks_url
        self.allow_anonymous_dev = allow_anonymous_dev

    @staticmethod
    def extract_credential(request: Request) -> tuple[str, bool]:
        """Return ``(credential, is_bearer)`` from ``X-API-Key`` or ``Authorization: Bearer``."""
        api_key_hdr = (request.headers.get("x-api-key") or "").strip()
        if api_key_hdr:
            return api_key_hdr, False
        auth_hdr = (request.headers.get("authorization") or "").strip()
        if auth_hdr.lower().startswith("bearer "):
            return auth_hdr[7:].strip(), True
        return "", False

    def _claims_to_context(self, claims: Mapping[str, Any]) -> TenantContext:
        user_id = str(claims.get("sub") or claims.get("user_id") or "").strip()
        tenant_id = str(claims.get("tenant_id") or claims.get("org_id") or "").strip()
        if not user_id or not tenant_id:
            raise ValueError("JWT missing required 'sub' or 'tenant_id' claims")
        role = str(claims.get("role") or "developer").strip().lower()
        raw_perms = claims.get("permissions") or claims.get("scope") or claims.get("scp")
        if isinstance(raw_perms, list):
            perms = frozenset(str(p) for p in raw_perms)
        elif isinstance(raw_perms, str) and raw_perms.strip():
            perms = frozenset(raw_perms.split())
        else:
            perms = DEFAULT_ROLE_PERMISSIONS.get(role, DEFAULT_ROLE_PERMISSIONS["viewer"])
        return TenantContext(
            tenant_id=tenant_id,
            user_id=user_id,
            role=role,
            permissions=perms,
            auth_method="jwt",
        )

    def _verify_jwt(self, token: str) -> TenantContext:
        if self._jwks_resolver is not None:
            decoded = self._jwks_resolver.verify_jwt(
                token,
                issuer=self.jwt_issuer,
                audience=self.jwt_audience,
            )
            return self._claims_to_context(decoded)
        if self.jwt_public_key:
            try:
                import jwt as pyjwt

                decode_options: Any = {"verify_aud": bool(self.jwt_audience)}
                decoded = pyjwt.decode(
                    token,
                    self.jwt_public_key,
                    algorithms=["RS256", "ES256"],
                    issuer=self.jwt_issuer,
                    audience=self.jwt_audience,
                    options=decode_options,
                )
                return self._claims_to_context(decoded)
            except ImportError as exc:
                raise ValueError("PyJWT required for asymmetric OIDC verification") from exc
        if self.jwt_secret:
            decoded = verify_hs256_jwt(
                token,
                self.jwt_secret,
                issuer=self.jwt_issuer,
                audience=self.jwt_audience,
            )
            return self._claims_to_context(decoded)
        raise ValueError("JWT verification not configured")

    def authenticate(self, request: Request) -> TenantContext:
        """Resolve and return verified ``TenantContext`` or raise HTTP 401."""
        credential, is_bearer = self.extract_credential(request)

        if not credential:
            if self.allow_anonymous_dev:
                dev_tenant = (request.headers.get("x-tenant-id") or "default").strip() or "default"
                return TenantContext(
                    tenant_id=dev_tenant,
                    user_id="dev-local",
                    role="admin",
                    permissions=DEFAULT_ROLE_PERMISSIONS["admin"],
                    auth_method="anonymous_dev",
                )
            raise HTTPException(status_code=401, detail="Authentication required")

        # 1. Constant-time API key match
        matched: TenantContext | None = None
        for registered_key, ctx in self._api_keys.items():
            if hmac.compare_digest(credential, registered_key):
                matched = ctx
        if matched is not None:
            return matched

        # 2. OIDC / JWT Bearer verification
        if is_bearer and credential.count(".") == 2:
            try:
                return self._verify_jwt(credential)
            except Exception as exc:
                logger.warning("JWT authentication rejected: %s", exc)
                raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

        raise HTTPException(status_code=401, detail="Invalid credentials")


class DistributedRateLimiter:
    """
    Multi-replica sliding-window rate limiter supporting Redis (when configured)
    with deterministic in-memory fallback for single-node dev and unit testing.
    """

    def __init__(
        self,
        *,
        max_requests: int = 120,
        window_seconds: float = 60.0,
        redis_client: Any | None = None,
        key_prefix: str = "pvs:ratelimit:",
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1.0, float(window_seconds))
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._local_hits: dict[str, deque[float]] = {}

    def set_redis_client(self, redis_client: Any | None) -> None:
        """Attach or replace the async Redis client after lifespan startup."""
        self._redis = redis_client

    async def check_and_record(self, key: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` across replicas."""
        if self._redis is not None:
            try:
                return await self._check_redis(key)
            except Exception as exc:
                logger.warning(
                    "Redis rate limiter unavailable (%s); falling back to local limiter", exc
                )
        return self._check_local(key)

    async def _check_redis(self, key: str) -> tuple[bool, int]:
        redis_client = self._redis
        assert redis_client is not None
        redis_key = f"{self._key_prefix}{key}"
        now = time.time()
        window_start = now - self.window_seconds

        async def _call(fn: Any, *args: Any) -> Any:
            res = fn(*args)
            return await res if inspect.isawaitable(res) else res

        await _call(redis_client.zremrangebyscore, redis_key, "-inf", window_start)
        count = int(await _call(redis_client.zcard, redis_key))
        if count >= self.max_requests:
            return False, max(1, int(self.window_seconds))
        member = f"{now:.6f}-{count}"
        await _call(redis_client.zadd, redis_key, {member: now})
        await _call(redis_client.expire, redis_key, int(self.window_seconds) + 5)
        return True, 0

    def _check_local(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket = self._local_hits.setdefault(key, deque())
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
            return False, retry_after
        bucket.append(now)
        return True, 0

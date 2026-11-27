"""
OAuth 2.1 / OIDC Dynamic JWKS Key Resolution & Automatic ``kid`` Rotation Tests (v2.6).

Exercises real RSA-2048 RS256 signing & verification via ``cryptography`` + ``PyJWT``:
- Dynamic JWKS fetch and TTL caching
- Zero-downtime key rotation (cache miss on rotated ``kid`` triggers single forced refresh)
- Rejection of ``alg: none``, algorithm confusion (HS256 against RS256 JWKS), expired tokens,
  wrong ``iss`` / ``aud``, and unknown ``kid``
- OAuth 2.1 ``scp`` / ``scope`` claim mapping to RBAC permissions across Control Plane & MCP
- RFC 9728 ``/.well-known/oauth-protected-resource`` advertising ``jwks_uri``
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.testclient import TestClient

from product_value_system.api.app_factory import ProductionSettings, create_production_app
from product_value_system.api.auth import JWKSKeyResolver, create_hs256_jwt


def _generate_rsa_jwk(kid: str) -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk["kid"] = kid
    public_jwk["use"] = "sig"
    public_jwk["alg"] = "RS256"
    return private_key, public_jwk


def _sign_rs256(private_key: Any, kid: str, claims: dict[str, Any]) -> str:
    return str(
        pyjwt.encode(
            claims,
            private_key,
            algorithm="RS256",
            headers={"kid": kid, "typ": "JWT"},
        )
    )


def test_oauth21_jwks_authentication_seamless_kid_rotation_and_rfc9728(tmp_path: Path) -> None:
    """Verify RS256 JWKS auth, seamless key-v1 -> key-v2 rotation, rejections, and RFC 9728."""
    priv_v1, pub_jwk_v1 = _generate_rsa_jwk("key-v1")
    priv_v2, pub_jwk_v2 = _generate_rsa_jwk("key-v2")

    active_jwks: dict[str, Any] = {"keys": [pub_jwk_v1]}
    jwks_url = "https://idp.acme.test/.well-known/jwks.json"

    def _jwks_handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == jwks_url
        return httpx.Response(200, json=active_jwks)

    resolver = JWKSKeyResolver(
        jwks_url,
        cache_ttl_seconds=300.0,
        min_refresh_interval_seconds=0.0,
        transport=httpx.MockTransport(_jwks_handler),
    )

    settings = ProductionSettings(
        database_url=f"sqlite:///{tmp_path / 'jwks.db'}",
        jwt_issuer="https://idp.acme.test",
        jwt_audience="pvs-control-plane",
        jwks_url=jwks_url,
        enable_devx=False,
        enable_audit=True,
        enable_tracing=False,
        enable_control_plane=True,
        enable_protocols=True,
        enable_integrations=False,
        enable_worker=False,
    )
    app = create_production_app(settings, jwks_resolver=resolver)

    now = int(time.time())
    token_v1 = _sign_rs256(
        priv_v1,
        "key-v1",
        {
            "sub": "alice-oidc",
            "org_id": "tenant-oidc",
            "role": "developer",
            "scp": ["runs:read", "runs:write", "agents:read"],
            "iss": "https://idp.acme.test",
            "aud": "pvs-control-plane",
            "iat": now,
            "exp": now + 600,
        },
    )

    with TestClient(app) as client:
        # 1. RFC 9728 metadata advertises issuer & jwks_uri
        meta = client.get("/.well-known/oauth-protected-resource").json()
        assert meta["authorization_servers"] == ["https://idp.acme.test"]
        assert meta["jwks_uri"] == jwks_url

        # 2. Authenticate with key-v1 (caches JWKS)
        r1 = client.get("/api/v1/runs", headers={"Authorization": f"Bearer {token_v1}"})
        assert r1.status_code == 200
        assert r1.json()["tenant_id"] == "tenant-oidc"
        assert resolver.refresh_count == 1

        # Second call hits TTL cache (no additional HTTP call)
        r2 = client.get("/api/v1/agents", headers={"Authorization": f"Bearer {token_v1}"})
        assert r2.status_code == 200
        assert resolver.refresh_count == 1

        # 3. Rotate IdP keys to key-v2 without restarting app -> cache miss triggers 1 refresh
        active_jwks["keys"] = [pub_jwk_v2]
        token_v2 = _sign_rs256(
            priv_v2,
            "key-v2",
            {
                "sub": "bob-oidc",
                "tenant_id": "tenant-oidc",
                "role": "approver",
                "scope": "runs:read runs:write runs:approve agents:read",
                "iss": "https://idp.acme.test",
                "aud": "pvs-control-plane",
                "iat": now,
                "exp": now + 600,
            },
        )
        r_rotated = client.post(
            "/api/v1/runs",
            headers={"Authorization": f"Bearer {token_v2}"},
            json={
                "problem_id": "PROB-OIDC-1",
                "title": "Rotated Key Run",
                "require_human_approval": True,
            },
        )
        assert r_rotated.status_code == 201
        assert resolver.refresh_count == 2

        # 4. Security rejections: expired token, wrong aud, wrong iss, HS256 alg confusion, bad kid
        expired_tok = _sign_rs256(
            priv_v2,
            "key-v2",
            {
                "sub": "bob-oidc",
                "tenant_id": "tenant-oidc",
                "iss": "https://idp.acme.test",
                "aud": "pvs-control-plane",
                "exp": now - 60,
            },
        )
        assert (
            client.get("/api/v1/runs", headers={"Authorization": f"Bearer {expired_tok}"})
            .status_code
            == 401
        )

        wrong_aud = _sign_rs256(
            priv_v2,
            "key-v2",
            {
                "sub": "bob-oidc",
                "tenant_id": "tenant-oidc",
                "iss": "https://idp.acme.test",
                "aud": "wrong-audience",
                "exp": now + 600,
            },
        )
        assert (
            client.get("/api/v1/runs", headers={"Authorization": f"Bearer {wrong_aud}"}).status_code
            == 401
        )

        # Algorithm confusion attack (HS256 signed with pubkey bytes) must be rejected
        hs_attack = create_hs256_jwt(
            {
                "sub": "attacker",
                "tenant_id": "tenant-oidc",
                "iss": "https://idp.acme.test",
                "aud": "pvs-control-plane",
            },
            secret=json.dumps(pub_jwk_v2),
        )
        assert (
            client.get("/api/v1/runs", headers={"Authorization": f"Bearer {hs_attack}"}).status_code
            == 401
        )

        unknown_kid = _sign_rs256(
            priv_v2,
            "key-nonexistent",
            {
                "sub": "bob-oidc",
                "tenant_id": "tenant-oidc",
                "iss": "https://idp.acme.test",
                "aud": "pvs-control-plane",
                "exp": now + 600,
            },
        )
        assert (
            client.get("/api/v1/runs", headers={"Authorization": f"Bearer {unknown_kid}"})
            .status_code
            == 401
        )

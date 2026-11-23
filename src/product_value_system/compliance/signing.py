"""
Cryptographic signing and tamper-detection primitives for compliance audit logs (PROD-006).

Implements RFC 2104 HMAC-SHA256 signing backed by ``cryptography`` and Python's
standard ``hmac``/``hashlib`` modules, canonical deterministic JSON serialization,
constant-time verification with key-rotation support, and SHA-256 hash chaining.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import constant_time, hashes
from cryptography.hazmat.primitives import hmac as crypto_hmac

_MIN_KEY_LENGTH_BYTES = 16


def _normalize_value(value: Any) -> Any:
    """Recursively normalize Python objects into canonical JSON-serializable values."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        items = sorted(value.items(), key=lambda i: str(i[0]))
        return {str(k): _normalize_value(v) for k, v in items}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize_value(item) for item in value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def canonicalize_payload(payload: Mapping[str, Any]) -> bytes:
    """Serialize an audit payload to deterministic UTF-8 JSON bytes.

    Keys are sorted recursively and whitespace is stripped so semantically
    identical payloads always produce identical HMAC-SHA256 digests.
    """
    normalized = _normalize_value(payload)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return canonical.encode("utf-8")


def generate_signing_key(num_bytes: int = 32) -> bytes:
    """Generate a cryptographically secure random HMAC signing key."""
    if num_bytes < _MIN_KEY_LENGTH_BYTES:
        raise ValueError(f"Signing key must be at least {_MIN_KEY_LENGTH_BYTES} bytes")
    return secrets.token_bytes(num_bytes)


def load_signing_key_from_env(
    env_var: str = "AUDIT_SIGNING_KEY",
    *,
    env: Mapping[str, str] | None = None,
) -> bytes:
    """Load an audit signing key from an environment variable or ``<VAR>_FILE`` mount.

    Accepts either a hex-encoded 64+ character secret or a raw UTF-8 secret string.
    Raises ``RuntimeError`` when the secret is missing or empty.
    """
    environ = os.environ if env is None else env
    raw = environ.get(env_var, "").strip()
    if not raw:
        file_path = environ.get(f"{env_var}_FILE", "").strip()
        if file_path:
            path = Path(file_path)
            if not path.is_file():
                raise RuntimeError(f"Audit signing key file not found: {path}")
            raw = path.read_text(encoding="utf-8").strip()

    if not raw:
        raise RuntimeError(
            f"Audit signing key not configured. Set {env_var} or {env_var}_FILE."
        )

    # Decode hex-encoded keys (e.g., `secrets.token_hex(32)`) cleanly; otherwise UTF-8 bytes.
    if len(raw) >= 32 and len(raw) % 2 == 0:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    return raw.encode("utf-8")


class AuditSigner:
    """HMAC-SHA256 signer and constant-time verifier with key-rotation support."""

    def __init__(
        self,
        signing_key: bytes | str,
        *,
        previous_keys: Sequence[bytes | str] = (),
    ) -> None:
        self._key = self._coerce_key(signing_key)
        self._previous_keys: tuple[bytes, ...] = tuple(
            self._coerce_key(k) for k in previous_keys
        )

    @staticmethod
    def _coerce_key(key: bytes | str) -> bytes:
        raw = key.encode("utf-8") if isinstance(key, str) else bytes(key)
        if not raw:
            raise ValueError("Audit signing key cannot be empty")
        return raw

    @property
    def key_fingerprint(self) -> str:
        """Non-sensitive 16-hex-char SHA-256 fingerprint of the active signing key."""
        return hashlib.sha256(self._key).hexdigest()[:16]

    def sign(self, payload: Mapping[str, Any]) -> str:
        """Return a 64-character lowercase hex HMAC-SHA256 signature for ``payload``."""
        message = canonicalize_payload(payload)
        mac = crypto_hmac.HMAC(self._key, hashes.SHA256())
        mac.update(message)
        return mac.finalize().hex()

    def verify(
        self,
        payload: Mapping[str, Any],
        signature: str | None,
        *,
        extra_keys: Sequence[bytes | str] = (),
    ) -> bool:
        """Verify ``signature`` against the active key or rotated legacy keys in constant time."""
        if not signature or len(signature) != 64:
            return False
        message = canonicalize_payload(payload)
        provided_bytes = signature.lower().encode("ascii", errors="ignore")
        if len(provided_bytes) != 64:
            return False

        candidate_keys = [
            self._key,
            *self._previous_keys,
            *(self._coerce_key(k) for k in extra_keys),
        ]
        matched = False
        for key in candidate_keys:
            expected_hex = hmac.new(key, message, hashlib.sha256).hexdigest().encode("ascii")
            if constant_time.bytes_eq(provided_bytes, expected_hex):
                matched = True
        return matched

    @staticmethod
    def compute_chain_hash(previous_hash: str, signature: str) -> str:
        """Link sequential audit signatures into a tamper-evident SHA-256 chain."""
        digest = hashlib.sha256()
        digest.update(previous_hash.encode("utf-8"))
        digest.update(b":")
        digest.update(signature.encode("utf-8"))
        return digest.hexdigest()


__all__ = [
    "AuditSigner",
    "canonicalize_payload",
    "generate_signing_key",
    "load_signing_key_from_env",
]

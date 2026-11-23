"""
Distributed tracing configuration, adaptive/probabilistic sampling, and privacy helpers (PROD-007).

Provides:
- ``TracingConfig``: 12-Factor environment-driven configuration (10% production sampling,
  100% development sampling, TLS default in production, ``OTEL_SDK_DISABLED`` kill-switch).
- ``build_sampler()``: Parent-based ratio sampler enforcing sampling invariants.
- ``hash_user_id()``: GDPR-compliant SHA-256 pseudonymization for trace user attributes.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass

from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    Sampler,
    TraceIdRatioBased,
)

from product_value_system.__version__ import __version__

_DEV_ENVIRONMENTS: frozenset[str] = frozenset({"dev", "development", "local", "test"})


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_sample_rate(environment: str) -> float:
    env_lower = environment.strip().lower()
    if env_lower in _DEV_ENVIRONMENTS:
        return 1.0
    if env_lower in {"staging", "stage", "preprod"}:
        return 0.25
    return 0.10


def hash_user_id(user_id: str, *, salt: str = "pvs-trace-uid") -> str:
    """Return a deterministic GDPR-safe pseudonymized user identifier for trace spans."""
    digest = hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:16]
    return f"usr_{digest}"


@dataclass(frozen=True)
class TracingConfig:
    """Immutable OpenTelemetry + Jaeger tracing configuration."""

    service_name: str = "product-value-system"
    service_version: str = __version__
    environment: str = "production"
    otlp_endpoint: str = "http://otel-collector:4317"
    otlp_insecure: bool = False
    sample_rate: float = 0.10
    disabled: bool = False
    redact_sensitive: bool = True
    hash_user_ids: bool = True
    batch_max_queue_size: int = 2048
    batch_max_export_batch_size: int = 512
    batch_schedule_delay_millis: int = 5000

    def __post_init__(self) -> None:
        if not (0.0 <= self.sample_rate <= 1.0):
            raise ValueError(f"sample_rate must be in [0.0, 1.0], got {self.sample_rate}")
        if self.batch_max_queue_size < 1:
            raise ValueError("batch_max_queue_size must be >= 1")
        if self.batch_max_export_batch_size < 1:
            raise ValueError("batch_max_export_batch_size must be >= 1")
        if self.batch_schedule_delay_millis < 1:
            raise ValueError("batch_schedule_delay_millis must be >= 1")

    @classmethod
    def from_env(
        cls,
        *,
        service_name: str | None = None,
        environment: str | None = None,
        otlp_endpoint: str | None = None,
        sample_rate: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> TracingConfig:
        """Build a ``TracingConfig`` from standard ``OTEL_*`` environment variables."""
        environ = os.environ if env is None else env
        resolved_env = (
            environment
            if environment is not None
            else environ.get("DEPLOYMENT_ENVIRONMENT")
            or environ.get("ENVIRONMENT")
            or "production"
        )
        resolved_service = (
            service_name
            if service_name is not None
            else environ.get("OTEL_SERVICE_NAME", "product-value-system")
        )
        resolved_version = environ.get("APP_VERSION", __version__)
        resolved_endpoint = (
            otlp_endpoint
            if otlp_endpoint is not None
            else environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
        )
        is_dev = resolved_env.strip().lower() in _DEV_ENVIRONMENTS
        resolved_insecure = _parse_bool(environ.get("OTEL_EXPORTER_OTLP_INSECURE"), default=is_dev)
        resolved_disabled = _parse_bool(environ.get("OTEL_SDK_DISABLED"), default=False)

        if sample_rate is not None:
            resolved_rate = float(sample_rate)
        else:
            raw_rate = environ.get("OTEL_TRACES_SAMPLER_ARG")
            if raw_rate is not None and raw_rate.strip() != "":
                resolved_rate = float(raw_rate.strip())
            else:
                resolved_rate = _default_sample_rate(resolved_env)

        return cls(
            service_name=resolved_service,
            service_version=resolved_version,
            environment=resolved_env,
            otlp_endpoint=resolved_endpoint,
            otlp_insecure=resolved_insecure,
            sample_rate=resolved_rate,
            disabled=resolved_disabled,
            redact_sensitive=_parse_bool(environ.get("OTEL_REDACT_SENSITIVE"), default=True),
            hash_user_ids=_parse_bool(environ.get("OTEL_HASH_USER_IDS"), default=True),
            batch_max_queue_size=int(environ.get("OTEL_BSP_MAX_QUEUE_SIZE", "2048")),
            batch_max_export_batch_size=int(environ.get("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "512")),
            batch_schedule_delay_millis=int(environ.get("OTEL_BSP_SCHEDULE_DELAY", "5000")),
        )


def build_sampler(config: TracingConfig) -> Sampler:
    """Construct the OpenTelemetry ``Sampler`` matching ``config``."""
    if config.disabled or config.sample_rate <= 0.0:
        return ALWAYS_OFF
    if config.sample_rate >= 1.0:
        return ParentBased(root=ALWAYS_ON)
    return ParentBased(root=TraceIdRatioBased(config.sample_rate))


__all__ = [
    "TracingConfig",
    "build_sampler",
    "hash_user_id",
]

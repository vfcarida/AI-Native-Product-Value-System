"""
Structured JSON logging with OpenTelemetry trace correlation, secret redaction,
and system environment diagnostics (Round 12).

Follows OpenTelemetry Logs Data Model & W3C Trace Context conventions:
- Emits UTC ISO-8601 JSON log envelopes with 32-char hex ``trace_id``,
  16-char hex ``span_id``, and ``run_id`` correlation context.
- Attaches ``SecretRedactingFilter`` directly to handlers so propagated child
  logger messages, format arguments, and structured ``extra`` fields cannot
  bypass credential redaction.
- Provides ``run_system_diagnostics()`` backing CLI ``pvs doctor`` health checks.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from opentelemetry import trace

from product_value_system.__version__ import __version__
from product_value_system.contracts.config_schemas import validate_all_configs
from product_value_system.observability.tracing import CURRENT_RUN_ID
from product_value_system.policy.engine import PolicyEngine

_STANDARD_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

_DEFAULT_REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?([^\s,'\"]{6,})['\"]?"
    ),
)


class SecretRedactingFilter(logging.Filter):
    """Logging filter that scrubs credentials and tokens before emission.

    Attach directly to a ``logging.Handler`` (not only to individual loggers) so
    all propagated records—including third-party library logs—are redacted.
    """

    def __init__(
        self,
        name: str = "",
        *,
        patterns: tuple[re.Pattern[str], ...] = _DEFAULT_REDACTION_PATTERNS,
        replacement: str = "[REDACTED]",
    ) -> None:
        super().__init__(name)
        self.patterns = patterns
        self.replacement = replacement

    def redact_text(self, value: str) -> str:
        """Scrub all matching secret patterns from ``value``."""
        scrubbed = value
        for pat in self.patterns:
            if pat.groups >= 2:
                scrubbed = pat.sub(rf"\1={self.replacement}", scrubbed)
            else:
                scrubbed = pat.sub(self.replacement, scrubbed)
        return scrubbed

    def _redact_obj(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.redact_text(obj)
        if isinstance(obj, dict):
            return {k: self._redact_obj(v) for k, v in obj.items()}
        if isinstance(obj, tuple):
            return tuple(self._redact_obj(v) for v in obj)
        if isinstance(obj, list):
            return [self._redact_obj(v) for v in obj]
        return obj

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact_text(record.msg)
        if record.args:
            record.args = self._redact_obj(record.args)
        for key, val in list(record.__dict__.items()):
            if key not in _STANDARD_RECORD_ATTRS and isinstance(val, (str, dict, list)):
                setattr(record, key, self._redact_obj(val))
        return True


class StructuredJsonFormatter(logging.Formatter):
    """OpenTelemetry-correlated JSON log formatter emitting W3C trace/span hex IDs."""

    def __init__(self, *, service_name: str = "product-value-system") -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname.lower(),
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx.is_valid:
            payload["trace_id"] = format(span_ctx.trace_id, "032x")
            payload["span_id"] = format(span_ctx.span_id, "016x")
        else:
            payload["trace_id"] = None
            payload["span_id"] = None

        run_id = CURRENT_RUN_ID.get()
        if run_id:
            payload["run_id"] = run_id

        extra_fields = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _STANDARD_RECORD_ATTRS and not k.startswith("_")
        }
        if extra_fields:
            payload["attributes"] = extra_fields

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(
    *,
    level: str | int = "INFO",
    json_output: bool = True,
    redact_secrets: bool = True,
    service_name: str = "product-value-system",
    stream: TextIO | None = None,
    logger_name: str | None = None,
) -> logging.Logger:
    """Configure a logger with structured JSON formatting and handler-level secret redaction."""
    target = logging.getLogger(logger_name)
    numeric_level = (
        getattr(logging, level.upper(), logging.INFO) if isinstance(level, str) else int(level)
    )
    target.setLevel(numeric_level)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setLevel(numeric_level)
    if json_output:
        handler.setFormatter(StructuredJsonFormatter(service_name=service_name))
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )

    if redact_secrets:
        handler.addFilter(SecretRedactingFilter())

    target.handlers.clear()
    target.addHandler(handler)
    return target


def run_system_diagnostics(repo_root: Path | str | None = None) -> dict[str, Any]:
    """Run non-destructive environment, config, policy, and schema diagnostics.

    Designed for both programmatic startup checks and CLI ``pvs doctor [--json]``.
    """
    base = Path(repo_root) if repo_root is not None else Path.cwd()
    checks: list[dict[str, Any]] = []

    # 1. Python runtime version
    py_ok = sys.version_info >= (3, 11)
    checks.append(
        {
            "name": "python_runtime",
            "ok": py_ok,
            "severity": "error" if not py_ok else "ok",
            "detail": f"Python {platform.python_version()} ({platform.system()})",
        }
    )

    # 2. Strict Pydantic v2 config validation across all config/*.yaml files
    config_dir = base / "config"
    if config_dir.is_dir():
        cfg_report = validate_all_configs(config_dir, expand_env=True)
        checks.append(
            {
                "name": "yaml_config_contracts",
                "ok": cfg_report.is_valid,
                "severity": "ok" if cfg_report.is_valid else "error",
                "detail": (
                    f"{len(cfg_report.validated_files)} configs valid"
                    if cfg_report.is_valid
                    else f"{len(cfg_report.errors)} config error(s): {sorted(cfg_report.errors)}"
                ),
            }
        )
    else:
        checks.append(
            {
                "name": "yaml_config_contracts",
                "ok": False,
                "severity": "error",
                "detail": f"Config directory not found: {config_dir}",
            }
        )

    # 3. Cedar PolicyEngine health
    policies_dir = base / "policies"
    try:
        engine = PolicyEngine(policies_dir=policies_dir)
        checks.append(
            {
                "name": "cedar_policy_engine",
                "ok": True,
                "severity": "ok",
                "detail": f"Loaded {engine.policies_file.name} (cached={engine._cache_hit})",
            }
        )
    except Exception as exc:
        checks.append(
            {
                "name": "cedar_policy_engine",
                "ok": False,
                "severity": "error",
                "detail": f"Failed to load policies: {exc}",
            }
        )

    # 4. JSON Schemas presence
    schemas_dir = base / "schemas"
    schema_files = list(schemas_dir.glob("*.json")) if schemas_dir.is_dir() else []
    schemas_ok = len(schema_files) > 0
    checks.append(
        {
            "name": "json_schemas",
            "ok": schemas_ok,
            "severity": "ok" if schemas_ok else "error",
            "detail": f"{len(schema_files)} schema file(s) in {schemas_dir}",
        }
    )

    # 5. Live LLM provider credentials (informational — mock mode works offline at $0)
    provider_env_checks: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("openai", ("OPENAI_API_KEY",)),
        ("anthropic", ("ANTHROPIC_API_KEY",)),
        ("gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        ("azure_openai", ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_AD_TOKEN")),
        ("ollama", ("OLLAMA_BASE_URL", "OLLAMA_HOST")),
    )
    providers_configured = [
        name
        for name, env_vars in provider_env_checks
        if any(os.getenv(var) for var in env_vars)
    ]
    checks.append(
        {
            "name": "llm_provider_credentials",
            "ok": True,
            "severity": "ok" if providers_configured else "info",
            "detail": (
                f"Configured providers: {', '.join(providers_configured)}"
                if providers_configured
                else "No live API keys set (offline mock mode ready at $0 cost)"
            ),
        }
    )

    healthy = all(c["ok"] for c in checks)
    return {
        "healthy": healthy,
        "version": __version__,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "repo_root": str(base.resolve()),
        "checks": checks,
    }

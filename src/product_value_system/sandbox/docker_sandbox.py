"""
Docker sandbox facade with integrated iptables egress filtering (EVO-029 / PROD-003).

Exports both:
- ``DockerSandboxProvider`` (async SandboxProvider used by agents/orchestrator)
- ``DockerSandbox`` (high-level synchronous code-execution sandbox with in-container
  iptables allowlist enforcement, dynamic allowlist reload, and graceful degradation)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from product_value_system.runtime.docker_sandbox import DockerSandboxProvider
from product_value_system.runtime.sandbox import ExecutionResult
from product_value_system.sandbox.network_filter import (
    DNSResolverFn,
    NetworkFilter,
    load_allowlist_from_config,
)

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWLIST_PATH = Path("config/network_allowlist.yaml")
_SUPPORTED_LANGUAGES: dict[str, list[str]] = {
    "python": ["python3", "-c"],
    "typescript": ["ts-node", "-e"],
    "javascript": ["node", "-e"],
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class DockerSandbox:
    """Isolated Docker code-execution sandbox enforcing iptables egress allowlists (PROD-003)."""

    def __init__(
        self,
        enable_network_filter: bool | None = None,
        *,
        allowlist_path: Path | str | None = None,
        client: Any | None = None,
        dns_resolver: DNSResolverFn | None = None,
        fallback_on_error: bool = True,
        log_denied: bool | None = None,
        mem_limit: str = "512m",
        cpu_period: int = 100000,
        cpu_quota: int = 50000,
    ) -> None:
        if client is not None:
            self.client: Any = client
        else:
            import docker

            self.client = docker.from_env()

        self.enable_network_filter: bool = (
            _env_bool("SANDBOX_NETWORK_FILTER_ENABLED", True)
            if enable_network_filter is None
            else bool(enable_network_filter)
        )
        resolved_allowlist: Path | str = (
            allowlist_path
            if allowlist_path is not None
            else os.getenv("SANDBOX_NETWORK_ALLOWLIST_PATH", str(_DEFAULT_ALLOWLIST_PATH))
        )
        self.allowlist_path: Path = Path(resolved_allowlist)
        self.fallback_on_error: bool = bool(fallback_on_error)
        self.log_denied: bool = (
            _env_bool("SANDBOX_NETWORK_FILTER_LOG_DENIED", True)
            if log_denied is None
            else bool(log_denied)
        )
        self.mem_limit = mem_limit
        self.cpu_period = cpu_period
        self.cpu_quota = cpu_quota
        self.last_filter_latency_ms: float | None = None

        if self.enable_network_filter:
            rules = load_allowlist_from_config(self.allowlist_path)
            self.network_filter: NetworkFilter | None = NetworkFilter(
                rules,
                dns_resolver=dns_resolver,
                log_blocked_connections=self.log_denied,
                fallback_on_error=self.fallback_on_error,
            )
        else:
            self.network_filter = None

    def reload_allowlist(self, config_path: Path | str | None = None) -> int:
        """Dynamically reload egress allowlist rules without rebuilding sandbox containers."""
        target = Path(config_path) if config_path is not None else self.allowlist_path
        if self.network_filter is None:
            rules = load_allowlist_from_config(target)
            self.network_filter = NetworkFilter(
                rules,
                log_blocked_connections=self.log_denied,
                fallback_on_error=self.fallback_on_error,
            )
            return len(rules)
        return self.network_filter.reload_allowlist(target)

    def execute_code(
        self,
        code: str,
        language: str = "python",
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute ``code`` in a hardened Docker container with optional iptables filtering."""
        lang = language.strip().lower()
        if lang not in _SUPPORTED_LANGUAGES:
            raise ValueError(
                f"Unsupported sandbox language {language!r}; "
                f"expected one of {sorted(_SUPPORTED_LANGUAGES)}"
            )

        container: Any | None = None
        started = time.perf_counter()
        try:
            create_kwargs: dict[str, Any] = {
                "image": f"sandbox-{lang}:latest",
                "mem_limit": self.mem_limit,
                "cpu_period": self.cpu_period,
                "cpu_quota": self.cpu_quota,
                "network_mode": "bridge",
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges"],
                "user": "1000:1000",
                "detach": True,
                "tty": True,
                "stdin_open": True,
            }
            if self.enable_network_filter and self.network_filter is not None:
                create_kwargs["cap_add"] = ["NET_ADMIN"]

            container = self.client.containers.create(**create_kwargs)
            container.start()

            if self.enable_network_filter and self.network_filter is not None:
                iptables_commands = self.network_filter.generate_iptables_rules()
                filter_latency_ms = NetworkFilter.apply_rules_in_container(
                    container,
                    iptables_commands,
                    fallback_on_error=self.fallback_on_error,
                )
                self.last_filter_latency_ms = filter_latency_ms
                logger.info(
                    "Sandbox network filter applied (%d rules) in %.2fms",
                    len(iptables_commands),
                    filter_latency_ms,
                )

            return self._execute_in_container(container, code, lang, timeout)
        finally:
            if container is not None:
                try:
                    container.stop(timeout=1)
                except Exception as exc:
                    logger.debug("Container stop ignored during cleanup: %s", exc)
                try:
                    container.remove(force=True)
                except TypeError:
                    container.remove()
                except Exception as exc:
                    logger.debug("Container remove ignored during cleanup: %s", exc)
            _ = time.perf_counter() - started

    @staticmethod
    def _execute_in_container(
        container: Any,
        code: str,
        language: str,
        timeout: int,
    ) -> ExecutionResult:
        cmd_prefix = _SUPPORTED_LANGUAGES[language]
        cmd = [*cmd_prefix, code]
        started = time.perf_counter()
        try:
            exec_res = container.exec_run(cmd, user="1000", demux=False)
        except TypeError:
            exec_res = container.exec_run(cmd)
        duration = time.perf_counter() - started

        if isinstance(exec_res, tuple):
            exit_code, output = exec_res
        else:
            exit_code = int(getattr(exec_res, "exit_code", 1))
            output = getattr(exec_res, "output", b"")

        decoded = (
            output.decode("utf-8", errors="replace")
            if isinstance(output, (bytes, bytearray))
            else str(output or "")
        )
        return ExecutionResult(
            exit_code=int(exit_code),
            stdout=decoded if int(exit_code) == 0 else "",
            stderr="" if int(exit_code) == 0 else decoded,
            duration_seconds=duration,
        )


__all__ = ["DockerSandbox", "DockerSandboxProvider"]

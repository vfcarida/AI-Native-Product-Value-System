"""
Sandbox security controls: network egress filtering, iptables chain management,
and DockerSandboxProvider facade (EVO-029).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from product_value_system.runtime.sandbox import (
    ExecutionResult,
    Sandbox,
    SandboxConfig,
    SandboxError,
    SandboxProvider,
    SandboxStatus,
)
from product_value_system.sandbox.iptables_manager import (
    CommandResult,
    IptablesError,
    IptablesManager,
    sanitize_chain_name,
    validate_chain_name,
)
from product_value_system.sandbox.network_filter import (
    DEFAULT_ALLOWLIST,
    AllowlistEntry,
    EgressDecision,
    NetworkFilter,
    NetworkFilterStatus,
    load_sandbox_network_config,
)

if TYPE_CHECKING:
    from product_value_system.runtime.docker_sandbox import DockerSandboxProvider


def __getattr__(name: str) -> Any:
    if name == "DockerSandboxProvider":
        from product_value_system.runtime.docker_sandbox import DockerSandboxProvider

        return DockerSandboxProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_ALLOWLIST",
    "AllowlistEntry",
    "CommandResult",
    "DockerSandboxProvider",
    "EgressDecision",
    "ExecutionResult",
    "IptablesError",
    "IptablesManager",
    "NetworkFilter",
    "NetworkFilterStatus",
    "Sandbox",
    "SandboxConfig",
    "SandboxError",
    "SandboxProvider",
    "SandboxStatus",
    "load_sandbox_network_config",
    "sanitize_chain_name",
    "validate_chain_name",
]

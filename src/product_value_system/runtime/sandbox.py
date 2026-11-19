"""
Sandbox provider protocol and types.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class SandboxStatus(str, Enum):
    """Sandbox lifecycle status."""

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class SandboxConfig:
    """Configuration for sandbox creation."""

    image: str = "pvs/agent-sandbox:latest"
    cpu_limit: float = 2.0  # CPU cores
    memory_limit_mb: int = 4096  # Memory in MB
    disk_limit_mb: int = 10240  # Disk in MB
    max_pids: int = 512  # Maximum processes
    timeout_seconds: int = 900  # 15 minutes
    network_enabled: bool = True
    network_mode: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)  # Allowlist for network egress


@dataclass
class ExecutionResult:
    """Result of command execution in sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass
class Sandbox:
    """Handle to a running sandbox."""

    id: str
    status: SandboxStatus
    config: SandboxConfig


@runtime_checkable
class SandboxProvider(Protocol):
    """
    Protocol for sandbox providers.

    Implementations MUST:
    - Run as non-root user (UID 1000)
    - Enforce resource limits
    - Isolate filesystem and network
    - Support timeout and kill
    """

    async def create(self, config: SandboxConfig) -> Sandbox:
        """
        Create isolated sandbox.

        Args:
            config: Sandbox configuration

        Returns:
            Sandbox handle

        Raises:
            SandboxError: If creation fails
        """
        ...

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        """
        Execute command in sandbox.

        Args:
            sandbox: Sandbox handle
            command: Shell command to execute
            timeout_seconds: Override default timeout

        Returns:
            ExecutionResult with exit code, stdout, stderr

        Raises:
            TimeoutError: If execution exceeds timeout
            SandboxError: If execution fails
        """
        ...

    async def copy_in(
        self,
        sandbox: Sandbox,
        local_path: Path,
        sandbox_path: Path,
    ) -> None:
        """Copy file/directory from host into sandbox."""
        ...

    async def copy_out(
        self,
        sandbox: Sandbox,
        sandbox_path: Path,
        local_path: Path,
    ) -> None:
        """Copy file/directory from sandbox to host."""
        ...

    async def destroy(self, sandbox: Sandbox) -> None:
        """Destroy sandbox and clean up resources."""
        ...


class SandboxError(Exception):
    """Sandbox operation error."""

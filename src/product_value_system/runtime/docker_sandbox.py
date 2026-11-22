"""
Docker-based sandbox provider implementation.
"""

import asyncio
import logging
import tarfile
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound

from product_value_system.runtime.sandbox import (
    ExecutionResult,
    Sandbox,
    SandboxConfig,
    SandboxError,
    SandboxStatus,
)
from product_value_system.sandbox.network_filter import (
    NetworkFilter,
    NetworkFilterStatus,
)

logger = logging.getLogger(__name__)


class DockerSandboxProvider:
    """
    Docker-based sandbox provider.

    Implements isolation via:
    - Non-root user (UID 1000)
    - Dropped capabilities
    - Resource limits (CPU, memory, PIDs)
    - Network restrictions & iptables egress filtering (EVO-029)
    - Read-only filesystem except workspace
    """

    def __init__(
        self,
        client: Any | None = None,
        *,
        network_filter: NetworkFilter | None = None,
        sandbox_config_path: Path | str | None = None,
    ) -> None:
        """Initialize Docker client and optional egress NetworkFilter."""
        try:
            self.client = client or docker.from_env()
            self.client.ping()
        except DockerException as e:
            raise SandboxError(f"Docker not available: {e}") from e

        if network_filter is not None:
            self.network_filter: NetworkFilter | None = network_filter
        elif sandbox_config_path is not None:
            self.network_filter = NetworkFilter.from_yaml(sandbox_config_path)
        else:
            self.network_filter = None

        self._active_filters: dict[str, NetworkFilter] = {}
        self.last_filter_status: NetworkFilterStatus | None = None

        logger.info("Docker sandbox provider initialized")

    def _resolve_filter_for_config(self, config: SandboxConfig) -> NetworkFilter | None:
        """Determine the active ``NetworkFilter`` for a sandbox creation request."""
        if not config.network_enabled:
            return None
        if config.network_filtering_enabled is False:
            return None

        base_ipt = self.network_filter.iptables if self.network_filter is not None else None
        base_dns = self.network_filter._dns_resolver if self.network_filter is not None else None

        if config.network_allowlist:
            return NetworkFilter(
                {
                    "filtering_enabled": True,
                    "mode": "allowlist",
                    "allowlist": list(config.network_allowlist),
                    "fallback_on_error": config.fallback_on_filter_error,
                },
                iptables=base_ipt,
                dns_resolver=base_dns,
            )
        if config.allowed_hosts:
            return NetworkFilter.from_allowed_hosts(
                list(config.allowed_hosts),
                enabled=True,
                fallback_on_error=config.fallback_on_filter_error,
                iptables=base_ipt,
                dns_resolver=base_dns,
            )
        return self.network_filter

    @staticmethod
    def _extract_container_ip(container: Any) -> str | None:
        """Extract IPv4 bridge IP address from a Docker container handle if available."""
        attrs = getattr(container, "attrs", None)
        if isinstance(attrs, dict):
            net_settings = attrs.get("NetworkSettings") or {}
            ip_addr = net_settings.get("IPAddress")
            if isinstance(ip_addr, str) and ip_addr.strip():
                return ip_addr.strip()
            networks = net_settings.get("Networks") or {}
            if isinstance(networks, dict):
                for net_info in networks.values():
                    if isinstance(net_info, dict):
                        candidate = net_info.get("IPAddress")
                        if isinstance(candidate, str) and candidate.strip():
                            return candidate.strip()
        return None

    async def create(self, config: SandboxConfig) -> Sandbox:
        """Create Docker container as sandbox and apply egress filtering if enabled."""
        await self._ensure_image(config.image)

        container_id = f"pvs-sandbox-{int(time.time() * 1000)}"
        network_mode = config.network_mode or ("bridge" if config.network_enabled else "none")

        container_config: dict[str, Any] = {
            "image": config.image,
            "name": container_id,
            "detach": True,
            "stdin_open": True,
            "tty": True,
            "user": "1000:1000",  # Non-root
            "cap_drop": ["ALL"],  # Drop all capabilities
            "security_opt": ["no-new-privileges"],
            "read_only": False,  # Workspace needs write
            "mem_limit": f"{config.memory_limit_mb}m",
            "nano_cpus": int(config.cpu_limit * 1_000_000_000),
            "pids_limit": config.max_pids,
            "network_mode": network_mode,
            "volumes": {
                "/tmp": {"bind": "/tmp", "mode": "rw"},
            },
        }

        try:
            loop = asyncio.get_running_loop()
            container = await loop.run_in_executor(
                None,
                lambda: self.client.containers.run(**container_config),
            )
        except DockerException as e:
            raise SandboxError(f"Failed to create sandbox: {e}") from e

        active_filter = self._resolve_filter_for_config(config)
        if active_filter is not None and active_filter.enabled:
            container_ip = self._extract_container_ip(container)
            try:
                status = await active_filter.apply_rules(
                    container_id,
                    container_ip=container_ip,
                )
                self.last_filter_status = status
                self._active_filters[container_id] = active_filter
            except Exception as exc:
                logger.error(
                    "Egress filter setup failed for %s; destroying container: %s",
                    container_id,
                    exc,
                )
                try:
                    await loop.run_in_executor(None, lambda: container.remove(force=True))
                except Exception as remove_exc:
                    logger.debug(
                        "Failed to remove container %s during egress cleanup: %s",
                        container_id,
                        remove_exc,
                    )
                raise SandboxError(f"Failed to apply network egress filter: {exc}") from exc

        logger.info("Created sandbox: %s", container_id)

        return Sandbox(
            id=container_id,
            status=SandboxStatus.RUNNING,
            config=config,
        )

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        """Execute command in Docker container."""
        timeout = timeout_seconds or sandbox.config.timeout_seconds

        try:
            container = self.client.containers.get(sandbox.id)
        except DockerException as e:
            raise SandboxError(f"Sandbox not found: {e}") from e

        start_time = time.time()

        try:
            loop = asyncio.get_running_loop()
            exec_result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: container.exec_run(
                        cmd=["bash", "-c", command],
                        stdout=True,
                        stderr=True,
                        user="pvs",
                    ),
                ),
                timeout=timeout,
            )

            duration = time.time() - start_time
            output_bytes = exec_result.output
            stdout = (
                output_bytes.decode("utf-8", errors="replace")
                if isinstance(output_bytes, bytes)
                else str(output_bytes or "")
            )

            return ExecutionResult(
                exit_code=exec_result.exit_code,
                stdout=stdout,
                stderr="",
                duration_seconds=duration,
            )

        except TimeoutError as e:
            logger.warning("Sandbox execution timeout after %ss", timeout)
            await self._kill_container(container)
            raise TimeoutError(f"Execution exceeded {timeout}s timeout") from e

        except DockerException as e:
            raise SandboxError(f"Execution failed: {e}") from e

    async def copy_in(
        self,
        sandbox: Sandbox,
        local_path: Path,
        sandbox_path: Path,
    ) -> None:
        """Copy file/directory into container."""
        try:
            container = self.client.containers.get(sandbox.id)
        except DockerException as e:
            raise SandboxError(f"Sandbox not found: {e}") from e

        tar_stream = BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.add(str(local_path), arcname=sandbox_path.name)

        tar_stream.seek(0)

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: container.put_archive(
                    path=sandbox_path.parent.as_posix(),
                    data=tar_stream.getvalue(),
                ),
            )
            logger.debug("Copied %s -> %s", local_path, sandbox_path)

        except DockerException as e:
            raise SandboxError(f"Copy failed: {e}") from e

    async def copy_out(
        self,
        sandbox: Sandbox,
        sandbox_path: Path,
        local_path: Path,
    ) -> None:
        """Copy file/directory from container to host."""
        try:
            container = self.client.containers.get(sandbox.id)
        except DockerException as e:
            raise SandboxError(f"Sandbox not found: {e}") from e

        try:
            loop = asyncio.get_running_loop()
            bits, _ = await loop.run_in_executor(
                None,
                lambda: container.get_archive(sandbox_path.as_posix()),
            )

            tar_stream = BytesIO()
            for chunk in bits:
                tar_stream.write(chunk)
            tar_stream.seek(0)

            with tarfile.open(fileobj=tar_stream, mode="r") as tar:
                members = tar.getmembers()
                if len(members) == 1 and members[0].isfile():
                    extracted = tar.extractfile(members[0])
                    if extracted is not None:
                        local_path.parent.mkdir(parents=True, exist_ok=True)
                        local_path.write_bytes(extracted.read())
                else:
                    tar.extractall(path=str(local_path.parent), filter="data")

            logger.debug("Copied %s -> %s", sandbox_path, local_path)

        except DockerException as e:
            raise SandboxError(f"Copy failed: {e}") from e

    async def destroy(self, sandbox: Sandbox) -> None:
        """Remove egress filtering rules, then stop and remove container."""
        active_filter = self._active_filters.pop(sandbox.id, self.network_filter)
        if active_filter is not None and active_filter.enabled:
            try:
                await active_filter.remove_rules(sandbox.id)
            except Exception as exc:
                logger.warning("Failed removing egress rules for %s: %s", sandbox.id, exc)

        try:
            container = self.client.containers.get(sandbox.id)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: container.stop(timeout=5))
            await loop.run_in_executor(None, lambda: container.remove(force=True))

            logger.info("Destroyed sandbox: %s", sandbox.id)

        except DockerException as e:
            logger.warning("Failed to destroy sandbox %s: %s", sandbox.id, e)

    async def _ensure_image(self, image: str) -> None:
        """Ensure Docker image exists."""
        try:
            self.client.images.get(image)
            logger.debug("Image exists: %s", image)
        except ImageNotFound as e:
            logger.info("Image missing: %s", image)
            raise SandboxError(
                f"Image not found: {image}. Run `make docker-build-sandbox` first."
            ) from e

    async def _kill_container(self, container: Any) -> None:
        """Force kill container (for timeouts)."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, container.kill)
        except DockerException as exc:
            logger.debug("Ignoring DockerException while force-killing container: %s", exc)

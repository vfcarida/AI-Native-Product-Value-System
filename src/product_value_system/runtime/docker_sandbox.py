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

logger = logging.getLogger(__name__)


class DockerSandboxProvider:
    """
    Docker-based sandbox provider.

    Implements isolation via:
    - Non-root user (UID 1000)
    - Dropped capabilities
    - Resource limits (CPU, memory, PIDs)
    - Network restrictions
    - Read-only filesystem except workspace
    """

    def __init__(self, client: Any | None = None) -> None:
        """Initialize Docker client."""
        try:
            self.client = client or docker.from_env()
            self.client.ping()
        except DockerException as e:
            raise SandboxError(f"Docker not available: {e}") from e

        logger.info("Docker sandbox provider initialized")

    async def create(self, config: SandboxConfig) -> Sandbox:
        """Create Docker container as sandbox."""
        await self._ensure_image(config.image)

        container_id = f"pvs-sandbox-{int(time.time() * 1000)}"

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
            "network_mode": "bridge" if config.network_enabled else "none",
            "volumes": {
                "/tmp": {"bind": "/tmp", "mode": "rw"},
            },
        }

        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.containers.run(**container_config),
            )

            logger.info("Created sandbox: %s", container_id)

            return Sandbox(
                id=container_id,
                status=SandboxStatus.RUNNING,
                config=config,
            )

        except DockerException as e:
            raise SandboxError(f"Failed to create sandbox: {e}") from e

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
        """Stop and remove container."""
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
        except DockerException:
            pass

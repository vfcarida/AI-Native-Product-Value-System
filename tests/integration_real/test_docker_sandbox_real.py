"""
Real integration tests against live Docker Engine and pvs/agent-sandbox:latest image (EVO-003).
"""

from collections.abc import AsyncIterator
from pathlib import Path

import docker
import pytest

from product_value_system.runtime.docker_sandbox import DockerSandboxProvider
from product_value_system.runtime.sandbox import SandboxConfig
from tests.integration_real.conftest import (
    docker_daemon_available,
    real_tests_enabled,
)


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled() or not docker_daemon_available(),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1 and active Docker daemon",
)
class TestDockerSandboxReal:
    """Real integration tests with Docker Engine."""

    @pytest.fixture
    async def sandbox_provider(self) -> AsyncIterator[DockerSandboxProvider]:
        """Create Docker sandbox provider and guarantee cleanup of leaked containers."""
        provider = DockerSandboxProvider()
        yield provider
        client = docker.from_env()
        for container in client.containers.list(all=True):
            if "pvs-sandbox" in (container.name or ""):
                container.remove(force=True)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_container_creation_and_cleanup(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test container creation, verification, and cleanup lifecycle."""
        config = SandboxConfig(
            image="pvs/agent-sandbox:latest",
            memory_limit_mb=512,
            timeout_seconds=30,
        )

        sandbox = await sandbox_provider.create(config)
        assert sandbox is not None

        client = docker.from_env()
        container = client.containers.get(sandbox.id)
        assert container.status in ["running", "created"]

        await sandbox_provider.destroy(sandbox)

        with pytest.raises(docker.errors.NotFound):
            client.containers.get(sandbox.id)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_command_execution(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test executing commands in real Docker sandbox."""
        config = SandboxConfig(image="pvs/agent-sandbox:latest")
        sandbox = await sandbox_provider.create(config)
        try:
            result = await sandbox_provider.execute(
                sandbox,
                "echo 'Hello from sandbox'",
                timeout_seconds=10,
            )
            assert result.exit_code == 0
            assert "Hello from sandbox" in result.stdout
        finally:
            await sandbox_provider.destroy(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_timeout_enforcement(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test command timeout terminates long-running commands."""
        config = SandboxConfig(image="pvs/agent-sandbox:latest")
        sandbox = await sandbox_provider.create(config)
        try:
            with pytest.raises(TimeoutError):
                await sandbox_provider.execute(
                    sandbox,
                    "sleep 30",
                    timeout_seconds=2,
                )
        finally:
            await sandbox_provider.destroy(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_memory_limit_enforcement(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test memory limit prevents excessive memory allocation."""
        config = SandboxConfig(
            image="pvs/agent-sandbox:latest",
            memory_limit_mb=128,
        )
        sandbox = await sandbox_provider.create(config)
        try:
            result = await sandbox_provider.execute(
                sandbox,
                "python3 -c 'x = bytearray(256 * 1024 * 1024)'",
                timeout_seconds=15,
            )
            assert result.exit_code != 0
        finally:
            await sandbox_provider.destroy(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_network_isolation_disabled(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test network_enabled=False blocks outbound connectivity."""
        config = SandboxConfig(
            image="pvs/agent-sandbox:latest",
            network_enabled=False,
        )
        sandbox = await sandbox_provider.create(config)
        try:
            result = await sandbox_provider.execute(
                sandbox,
                "python3 -c 'import urllib.request; "
                "urllib.request.urlopen(\"https://example.com\", timeout=3)'",
                timeout_seconds=10,
            )
            assert result.exit_code != 0
        finally:
            await sandbox_provider.destroy(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_file_copy_in_out(
        self, sandbox_provider: DockerSandboxProvider, tmp_path: Path
    ) -> None:
        """Test file transfer to and from real Docker container."""
        config = SandboxConfig(image="pvs/agent-sandbox:latest")
        sandbox = await sandbox_provider.create(config)
        try:
            test_file = tmp_path / "test.txt"
            test_file.write_text("Hello from host", encoding="utf-8")

            await sandbox_provider.copy_in(
                sandbox,
                test_file,
                Path("/workspace/test.txt"),
            )

            result = await sandbox_provider.execute(
                sandbox,
                "cat /workspace/test.txt",
                timeout_seconds=5,
            )
            assert "Hello from host" in result.stdout

            await sandbox_provider.execute(
                sandbox,
                "echo 'Modified' > /workspace/output.txt",
                timeout_seconds=5,
            )

            output_file = tmp_path / "output.txt"
            await sandbox_provider.copy_out(
                sandbox,
                Path("/workspace/output.txt"),
                output_file,
            )
            assert output_file.read_text(encoding="utf-8").strip() == "Modified"
        finally:
            await sandbox_provider.destroy(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_security_non_root(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test container runs commands as non-root user."""
        config = SandboxConfig(image="pvs/agent-sandbox:latest")
        sandbox = await sandbox_provider.create(config)
        try:
            result = await sandbox_provider.execute(
                sandbox,
                "id -u",
                timeout_seconds=5,
            )
            assert result.exit_code == 0
            assert int(result.stdout.strip()) != 0
        finally:
            await sandbox_provider.destroy(sandbox)

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_capability_drop(
        self, sandbox_provider: DockerSandboxProvider
    ) -> None:
        """Test Linux capabilities (e.g., CAP_NET_RAW) are dropped."""
        config = SandboxConfig(image="pvs/agent-sandbox:latest")
        sandbox = await sandbox_provider.create(config)
        try:
            result = await sandbox_provider.execute(
                sandbox,
                "python3 -c 'import socket; "
                "socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)'",
                timeout_seconds=5,
            )
            assert result.exit_code != 0
        finally:
            await sandbox_provider.destroy(sandbox)

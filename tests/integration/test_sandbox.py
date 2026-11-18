"""
Sandbox integration tests (runs against real Docker daemon when available).
"""

import tempfile
from pathlib import Path

import pytest

from product_value_system.runtime.docker_sandbox import DockerSandboxProvider
from product_value_system.runtime.sandbox import SandboxConfig


@pytest.fixture
def sandbox_provider() -> DockerSandboxProvider:
    """Docker sandbox provider instance (skips if Docker daemon is not running)."""
    try:
        provider = DockerSandboxProvider()
        provider.client.images.get("pvs/agent-sandbox:latest")
        return provider
    except Exception as exc:
        pytest.skip(f"Docker daemon or pvs/agent-sandbox:latest image not available: {exc}")


@pytest.fixture
def sandbox_config() -> SandboxConfig:
    """Default sandbox configuration."""
    return SandboxConfig(
        image="pvs/agent-sandbox:latest",
        cpu_limit=1.0,
        memory_limit_mb=512,
        timeout_seconds=30,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sandbox_creates_and_destroys(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test sandbox creation and destruction."""
    sandbox = await sandbox_provider.create(sandbox_config)

    assert sandbox.id is not None
    assert sandbox.id.startswith("pvs-sandbox-")

    await sandbox_provider.destroy(sandbox)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sandbox_executes_command(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test command execution in sandbox."""
    sandbox = await sandbox_provider.create(sandbox_config)

    try:
        result = await sandbox_provider.execute(sandbox, "echo 'Hello, sandbox!'")

        assert result.exit_code == 0
        assert "Hello, sandbox!" in result.stdout
        assert result.duration_seconds > 0

    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sandbox_runs_as_non_root(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test that sandbox runs as non-root user (UID 1000)."""
    sandbox = await sandbox_provider.create(sandbox_config)

    try:
        result = await sandbox_provider.execute(sandbox, "id -u")

        assert result.exit_code == 0
        assert result.stdout.strip() == "1000"

    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sandbox_enforces_timeout(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test that long-running commands are killed after timeout."""
    sandbox_config.timeout_seconds = 2
    sandbox = await sandbox_provider.create(sandbox_config)

    try:
        with pytest.raises(TimeoutError):
            await sandbox_provider.execute(sandbox, "sleep 10")

    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sandbox_copy_in_and_out(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test copying files into and out of sandbox."""
    sandbox = await sandbox_provider.create(sandbox_config)
    local_file: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("Test content")
            local_file = Path(f.name)

        await sandbox_provider.copy_in(
            sandbox,
            local_file,
            Path("/workspace/test.txt"),
        )

        result = await sandbox_provider.execute(
            sandbox,
            "cat /workspace/test.txt",
        )
        assert "Test content" in result.stdout

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "output.txt"
            await sandbox_provider.copy_out(
                sandbox,
                Path("/workspace/test.txt"),
                out_path,
            )

            assert out_path.exists()

    finally:
        await sandbox_provider.destroy(sandbox)
        if local_file is not None:
            local_file.unlink(missing_ok=True)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_sandbox_isolated_filesystem(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test that sandbox cannot access host filesystem."""
    sandbox = await sandbox_provider.create(sandbox_config)

    try:
        result = await sandbox_provider.execute(sandbox, "ls /etc/passwd")
        assert result.exit_code == 0

        result = await sandbox_provider.execute(sandbox, "touch /root/test.txt")
        assert result.exit_code != 0

    finally:
        await sandbox_provider.destroy(sandbox)

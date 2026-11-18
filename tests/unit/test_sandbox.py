"""
Unit tests for P005 Sandbox Docker Provider (deterministic, offline-compatible).
"""

import tarfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from docker.errors import ImageNotFound

from product_value_system.runtime.docker_sandbox import DockerSandboxProvider
from product_value_system.runtime.sandbox import (
    SandboxConfig,
    SandboxError,
    SandboxProvider,
    SandboxStatus,
)


@dataclass
class FakeExecResult:
    """Fake Docker exec_run result."""

    exit_code: int
    output: bytes


class FakeContainer:
    """Fake Docker container simulating non-root UID 1000 sandbox."""

    def __init__(self, name: str, kwargs: dict[str, Any]) -> None:
        self.id = name
        self.name = name
        self.kwargs = kwargs
        self.files: dict[str, bytes] = {"/etc/passwd": b"pvs:x:1000:1000::/home/pvs:/bin/bash\n"}
        self.killed = False
        self.stopped = False
        self.removed = False

    def exec_run(
        self,
        cmd: list[str],
        stdout: bool = True,
        stderr: bool = True,
        user: str = "pvs",
    ) -> FakeExecResult:
        command = cmd[-1]
        if command.startswith("sleep"):
            seconds = float(command.split()[1])
            time.sleep(seconds)
            return FakeExecResult(0, b"")
        if command == "id -u":
            return FakeExecResult(0, b"1000\n")
        if command.startswith("echo "):
            text = command.removeprefix("echo ").strip("'\"")
            return FakeExecResult(0, f"{text}\n".encode())
        if command.startswith("cat "):
            target = command.removeprefix("cat ").strip()
            if target in self.files:
                return FakeExecResult(0, self.files[target])
            return FakeExecResult(1, b"No such file")
        if command == "ls /etc/passwd":
            return FakeExecResult(0, b"/etc/passwd\n")
        if command.startswith("touch /root/"):
            return FakeExecResult(1, b"Permission denied")
        return FakeExecResult(0, b"ok\n")

    def put_archive(self, path: str, data: bytes) -> bool:
        stream = BytesIO(data)
        with tarfile.open(fileobj=stream, mode="r") as tar:
            for member in tar.getmembers():
                extracted = tar.extractfile(member)
                if extracted is not None:
                    full_path = f"{path.rstrip('/')}/{member.name}"
                    self.files[full_path] = extracted.read()
        return True

    def get_archive(self, path: str) -> tuple[list[bytes], dict[str, Any]]:
        content = self.files.get(path, b"")
        stream = BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            info = tarfile.TarInfo(name=Path(path).name)
            info.size = len(content)
            tar.addfile(info, BytesIO(content))
        return [stream.getvalue()], {"name": Path(path).name, "size": len(content)}

    def kill(self) -> None:
        self.killed = True

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True

    def remove(self, force: bool = True) -> None:
        self.removed = True


class FakeImages:
    """Fake Docker images collection."""

    def __init__(self, available: set[str] | None = None) -> None:
        self.available = available or {"pvs/agent-sandbox:latest"}

    def get(self, name: str) -> dict[str, str]:
        if name not in self.available:
            raise ImageNotFound(f"Image not found: {name}")
        return {"id": name}


class FakeContainers:
    """Fake Docker containers collection."""

    def __init__(self) -> None:
        self._items: dict[str, FakeContainer] = {}

    def run(self, **kwargs: Any) -> FakeContainer:
        name = kwargs["name"]
        container = FakeContainer(name=name, kwargs=kwargs)
        self._items[name] = container
        return container

    def get(self, container_id: str) -> FakeContainer:
        return self._items[container_id]


class FakeDockerClient:
    """Fake Docker SDK client."""

    def __init__(self, available_images: set[str] | None = None) -> None:
        self.images = FakeImages(available_images)
        self.containers = FakeContainers()

    def ping(self) -> bool:
        return True


@pytest.fixture
def fake_client() -> FakeDockerClient:
    """Fake Docker client instance."""
    return FakeDockerClient()


@pytest.fixture
def sandbox_provider(fake_client: FakeDockerClient) -> DockerSandboxProvider:
    """DockerSandboxProvider backed by FakeDockerClient."""
    return DockerSandboxProvider(client=fake_client)


@pytest.fixture
def sandbox_config() -> SandboxConfig:
    """Default sandbox configuration."""
    return SandboxConfig(
        image="pvs/agent-sandbox:latest",
        cpu_limit=1.0,
        memory_limit_mb=512,
        timeout_seconds=30,
        network_enabled=False,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_creates_with_security_constraints_and_destroys(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
    fake_client: FakeDockerClient,
) -> None:
    """Test sandbox creation security parameters and destruction."""
    provider_proto: SandboxProvider = sandbox_provider
    sandbox = await provider_proto.create(sandbox_config)

    assert sandbox.id.startswith("pvs-sandbox-")
    assert sandbox.status == SandboxStatus.RUNNING

    container = fake_client.containers.get(sandbox.id)
    assert container.kwargs["user"] == "1000:1000"
    assert container.kwargs["cap_drop"] == ["ALL"]
    assert container.kwargs["security_opt"] == ["no-new-privileges"]
    assert container.kwargs["mem_limit"] == "512m"
    assert container.kwargs["pids_limit"] == 512
    assert container.kwargs["network_mode"] == "none"

    await sandbox_provider.destroy(sandbox)
    assert container.stopped is True
    assert container.removed is True


@pytest.mark.unit
@pytest.mark.asyncio
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
        assert result.duration_seconds >= 0
    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.unit
@pytest.mark.asyncio
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_enforces_timeout(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
    fake_client: FakeDockerClient,
) -> None:
    """Test that long-running commands are killed after timeout."""
    sandbox_config.timeout_seconds = 1
    sandbox = await sandbox_provider.create(sandbox_config)
    try:
        with pytest.raises(TimeoutError):
            await sandbox_provider.execute(sandbox, "sleep 3", timeout_seconds=1)
        container = fake_client.containers.get(sandbox.id)
        assert container.killed is True
    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_copy_in_and_out(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
    tmp_path: Path,
) -> None:
    """Test copying files into and out of sandbox."""
    sandbox = await sandbox_provider.create(sandbox_config)
    try:
        local_file = tmp_path / "input.txt"
        local_file.write_text("Test content", encoding="utf-8")

        await sandbox_provider.copy_in(
            sandbox,
            local_file,
            Path("/workspace/test.txt"),
        )

        result = await sandbox_provider.execute(sandbox, "cat /workspace/test.txt")
        assert "Test content" in result.stdout

        out_path = tmp_path / "output.txt"
        await sandbox_provider.copy_out(
            sandbox,
            Path("/workspace/test.txt"),
            out_path,
        )
        assert out_path.exists()
        assert out_path.read_text(encoding="utf-8") == "Test content"
    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_isolated_filesystem(
    sandbox_provider: DockerSandboxProvider,
    sandbox_config: SandboxConfig,
) -> None:
    """Test that sandbox cannot write to root-owned host/system paths."""
    sandbox = await sandbox_provider.create(sandbox_config)
    try:
        result = await sandbox_provider.execute(sandbox, "ls /etc/passwd")
        assert result.exit_code == 0

        result = await sandbox_provider.execute(sandbox, "touch /root/test.txt")
        assert result.exit_code != 0
    finally:
        await sandbox_provider.destroy(sandbox)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_missing_image_raises_sandbox_error(
    sandbox_provider: DockerSandboxProvider,
) -> None:
    """Test that missing sandbox image raises actionable SandboxError."""
    bad_config = SandboxConfig(image="pvs/nonexistent:latest")
    with pytest.raises(SandboxError, match="make docker-build-sandbox"):
        await sandbox_provider.create(bad_config)

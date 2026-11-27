"""
Tests for Kernel-Level Multi-Tenant Sandbox Providers (v3.0 — Stage 4):
- ``OCIHardenedRuntimeProvider`` (gVisor ``runsc`` & Kata MicroVM ``kata-runtime``)
- Fail-closed runtime verification (refuses insecure silent downgrade to ``runc``)
- Zip-Slip / symlink archive extraction protection
- ``E2BMicroVMSandboxProvider`` (Firecracker MicroVM async REST client)
- ``create_sandbox_provider()`` unified factory
"""

from __future__ import annotations

import base64
import tarfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest

from product_value_system.runtime import (
    E2BMicroVMSandboxProvider,
    KernelIsolationRuntime,
    MicroVMSandboxConfig,
    OCIHardenedRuntimeProvider,
    SandboxError,
    SandboxProvider,
    SandboxStatus,
    create_sandbox_provider,
)


@dataclass
class _FakeExecRes:
    exit_code: int
    output: tuple[bytes, bytes]


class _FakeContainer:
    def __init__(self, cid: str) -> None:
        self.id = cid
        self.removed = False
        self.uploaded: list[tuple[str, bytes]] = []
        self.archive_bytes: bytes = b""

    def exec_run(self, _cmd: list[str], **_kwargs: Any) -> _FakeExecRes:
        return _FakeExecRes(exit_code=0, output=(b"hello from microvm\n", b""))

    def put_archive(self, path: str, data: bytes) -> bool:
        self.uploaded.append((path, data))
        return True

    def get_archive(self, _path: str) -> tuple[list[bytes], dict[str, Any]]:
        return [self.archive_bytes], {"name": "out.txt"}

    def remove(self, force: bool = False) -> None:
        assert force is True
        self.removed = True


class _FakeContainersAPI:
    def __init__(self) -> None:
        self.run_kwargs: list[dict[str, Any]] = []
        self.containers: dict[str, _FakeContainer] = {}

    def run(self, **kwargs: Any) -> _FakeContainer:
        self.run_kwargs.append(kwargs)
        c = _FakeContainer(f"ctr-{len(self.containers) + 1}")
        self.containers[c.id] = c
        return c

    def get(self, cid: str) -> _FakeContainer:
        return self.containers[cid]


class _FakeDockerClient:
    def __init__(self, runtimes: dict[str, Any]) -> None:
        self._runtimes = runtimes
        self.containers = _FakeContainersAPI()

    def info(self) -> dict[str, Any]:
        return {"Runtimes": self._runtimes}

    def ping(self) -> bool:
        return True


def _make_tar_bytes(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, BytesIO(data))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_oci_hardened_runtime_enforces_gvisor_kata_restricted_pss_and_fail_closed(
    tmp_path: Path,
) -> None:
    """OCIHardenedRuntimeProvider enforces gVisor/Kata runtime, fails closed, blocks zip-slip."""
    # 1. Fail-closed when runsc is NOT registered on daemon
    plain_daemon = _FakeDockerClient(runtimes={"runc": {}})
    strict_gvisor = OCIHardenedRuntimeProvider(
        isolation_runtime=KernelIsolationRuntime.GVISOR,
        client=plain_daemon,
        allow_runc_fallback=False,
    )
    assert isinstance(strict_gvisor, SandboxProvider)
    with pytest.raises(SandboxError, match="Refusing insecure downgrade to runc"):
        await strict_gvisor.create(MicroVMSandboxConfig(tenant_id="tenant-a"))

    # Explicit opt-in fallback allowed for local dev
    fallback_provider = OCIHardenedRuntimeProvider(
        isolation_runtime=KernelIsolationRuntime.GVISOR,
        client=plain_daemon,
        allow_runc_fallback=True,
    )
    fb_sb = await fallback_provider.create(MicroVMSandboxConfig(tenant_id="tenant-dev"))
    assert plain_daemon.containers.run_kwargs[-1]["runtime"] == "runc"
    await fallback_provider.destroy(fb_sb)

    # 2. Full Kata MicroVM lifecycle with Restricted PSS & tenant labels
    kata_daemon = _FakeDockerClient(runtimes={"runc": {}, "runsc": {}, "kata-runtime": {}})
    kata_provider = OCIHardenedRuntimeProvider(
        isolation_runtime=KernelIsolationRuntime.KATA,
        client=kata_daemon,
    )
    cfg = MicroVMSandboxConfig(
        isolation_runtime=KernelIsolationRuntime.KATA,
        tenant_id="tenant-regulated",
        network_enabled=False,
        cpu_limit=1.5,
        memory_limit_mb=2048,
        max_pids=128,
    )
    sb = await kata_provider.create(cfg)
    assert sb.status == SandboxStatus.RUNNING

    run_args = kata_daemon.containers.run_kwargs[0]
    assert run_args["runtime"] == "kata-runtime"
    assert run_args["user"] == "1000:1000"
    assert run_args["read_only"] is True
    assert run_args["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in run_args["security_opt"]
    assert run_args["network_mode"] == "none"
    assert run_args["labels"]["pvs.tenant_id"] == "tenant-regulated"
    assert run_args["labels"]["pvs.isolation_runtime"] == "kata-runtime"

    res = await kata_provider.execute(sb, "python --version", timeout_seconds=10)
    assert res.exit_code == 0
    assert "hello from microvm" in res.stdout

    # copy_in & safe copy_out
    src_file = tmp_path / "input.py"
    src_file.write_text("print(42)\n", encoding="utf-8")
    await kata_provider.copy_in(sb, src_file, Path("/workspace/input.py"))
    assert len(kata_daemon.containers.get(sb.id).uploaded) == 1

    ctr = kata_daemon.containers.get(sb.id)
    ctr.archive_bytes = _make_tar_bytes({"safe_out.txt": b"ok"})
    out_target = tmp_path / "extracted" / "safe_out.txt"
    await kata_provider.copy_out(sb, Path("/workspace/safe_out.txt"), out_target)
    assert out_target.read_text(encoding="utf-8") == "ok"

    # Zip-Slip archive member MUST be rejected
    ctr.archive_bytes = _make_tar_bytes({"../../evil.txt": b"pwned"})
    with pytest.raises(SandboxError, match="Path traversal detected"):
        await kata_provider.copy_out(sb, Path("/workspace/evil"), out_target)

    await kata_provider.destroy(sb)
    assert sb.status == SandboxStatus.STOPPED
    assert ctr.removed is True


@pytest.mark.asyncio
async def test_e2b_firecracker_microvm_provider_and_factory(tmp_path: Path) -> None:
    """E2BMicroVMSandboxProvider provisions Firecracker microVMs, runs commands, copies files."""
    files_store: dict[str, str] = {}

    def _e2b_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-e2b-api-key"
        if request.method == "POST" and request.url.path == "/sandboxes":
            body = request.read().decode("utf-8")
            assert '"tenant_id":"tenant-vm"' in body.replace(" ", "")
            return httpx.Response(201, json={"sandboxID": "e2b-vm-001"})
        if request.method == "POST" and request.url.path == "/sandboxes/e2b-vm-001/commands":
            return httpx.Response(
                200,
                json={"exitCode": 0, "stdout": "pytest: 3 passed", "stderr": ""},
            )
        if request.method == "POST" and request.url.path == "/sandboxes/e2b-vm-001/files":
            import json

            data = json.loads(request.content.decode("utf-8"))
            files_store[data["path"]] = data["content_base64"]
            return httpx.Response(200, json={"ok": True})
        if request.method == "GET" and request.url.path == "/sandboxes/e2b-vm-001/files":
            path_q = str(request.url.params.get("path", ""))
            return httpx.Response(200, json={"content_base64": files_store.get(path_q, "")})
        if request.method == "DELETE" and request.url.path == "/sandboxes/e2b-vm-001":
            return httpx.Response(204)
        return httpx.Response(404)

    provider = create_sandbox_provider(
        "e2b",
        api_key="test-e2b-api-key",
        transport=httpx.MockTransport(_e2b_handler),
    )
    assert isinstance(provider, E2BMicroVMSandboxProvider)
    assert isinstance(provider, SandboxProvider)

    try:
        sb = await provider.create(
            MicroVMSandboxConfig(
                isolation_runtime=KernelIsolationRuntime.E2B,
                tenant_id="tenant-vm",
                e2b_template="python-3.12-hardened",
            )
        )
        assert sb.id == "e2b-vm-001"
        exec_res = await provider.execute(sb, "pytest -q")
        assert exec_res.exit_code == 0 and "3 passed" in exec_res.stdout

        local_in = tmp_path / "app.py"
        local_in.write_bytes(b"x = 1\n")
        await provider.copy_in(sb, local_in, Path("/workspace/app.py"))
        assert base64.b64decode(files_store["/workspace/app.py"]) == b"x = 1\n"

        local_out = tmp_path / "dl" / "app.py"
        await provider.copy_out(sb, Path("/workspace/app.py"), local_out)
        assert local_out.read_bytes() == b"x = 1\n"

        await provider.destroy(sb)
        assert sb.status == SandboxStatus.STOPPED
    finally:
        await provider.close()

    with pytest.raises(ValueError, match="Unknown sandbox provider"):
        create_sandbox_provider("unsupported-runtime")

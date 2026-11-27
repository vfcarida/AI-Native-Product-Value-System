"""
Kernel-Level Multi-Tenant Sandbox Isolation Providers (v3.0 — Stage 4):
- ``OCIHardenedRuntimeProvider``: Enforces gVisor (``runsc``) or Kata Containers
  (``kata-runtime``) MicroVM runtimes over OCI/Docker with fail-closed runtime
  verification, Restricted PSS (UID 1000, ``cap_drop=["ALL"]``, ``read_only``,
  ``no-new-privileges``, seccomp), and multi-tenant labels.
- ``E2BMicroVMSandboxProvider``: Async Firecracker MicroVM sandbox provider
  communicating with the E2B REST API via ``httpx.AsyncClient`` (Vault-backed
  ``E2B_API_KEY`` + injectable transport for deterministic ``$0`` testing).
- ``create_sandbox_provider()``: Unified runtime factory driven by
  ``SANDBOX_PROVIDER`` (``gvisor`` | ``kata`` | ``e2b`` | ``docker``).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import tarfile
import time
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from product_value_system.runtime.sandbox import (
    ExecutionResult,
    Sandbox,
    SandboxConfig,
    SandboxError,
    SandboxProvider,
    SandboxStatus,
)
from product_value_system.secrets.vault_client import resolve_secret

logger = logging.getLogger(__name__)


class KernelIsolationRuntime(str, Enum):
    """Supported kernel-level sandbox isolation runtimes."""

    GVISOR = "runsc"
    KATA = "kata-runtime"
    FIRECRACKER = "firecracker"
    E2B = "e2b"


@dataclass
class MicroVMSandboxConfig(SandboxConfig):
    """Extended sandbox configuration enforcing kernel-level multi-tenant isolation."""

    isolation_runtime: KernelIsolationRuntime = KernelIsolationRuntime.GVISOR
    tenant_id: str = "default"
    read_only_rootfs: bool = True
    seccomp_profile: str = "runtime/default"
    drop_capabilities: tuple[str, ...] = ("ALL",)
    no_new_privileges: bool = True
    e2b_template: str = "python-3.12"


def _safe_extract_tar(raw_bytes: bytes, dest_dir: Path) -> None:
    """Safely extract tar bytes preventing Zip-Slip / path traversal outside ``dest_dir``."""
    resolved_dest = dest_dir.resolve()
    with tarfile.open(fileobj=BytesIO(raw_bytes), mode="r:*") as tar:
        for member in tar.getmembers():
            if member.issym() or member.islnk():
                raise SandboxError(f"Refusing to extract symlink from sandbox: {member.name!r}")
            target = (resolved_dest / member.name).resolve()
            if resolved_dest != target and resolved_dest not in target.parents:
                raise SandboxError(
                    f"Path traversal detected in sandbox archive member: {member.name!r}"
                )
        tar.extractall(path=resolved_dest, filter="data")


class OCIHardenedRuntimeProvider:
    """
    Hardened OCI/Docker sandbox provider enforcing kernel-level isolation via
    gVisor (``runsc``) or Kata Containers MicroVMs (``kata-runtime``).

    Fails closed if the configured OCI runtime is unavailable on the host daemon
    unless ``allow_runc_fallback=True`` is explicitly requested for local dev.
    """

    def __init__(
        self,
        *,
        isolation_runtime: KernelIsolationRuntime = KernelIsolationRuntime.GVISOR,
        client: Any | None = None,
        allow_runc_fallback: bool = False,
    ) -> None:
        if isolation_runtime not in {
            KernelIsolationRuntime.GVISOR,
            KernelIsolationRuntime.KATA,
        }:
            raise ValueError(
                f"OCIHardenedRuntimeProvider requires GVISOR or KATA, got {isolation_runtime!r}"
            )
        self.isolation_runtime = isolation_runtime
        self.allow_runc_fallback = allow_runc_fallback
        if client is not None:
            self.client = client
        else:
            try:
                import docker

                self.client = docker.from_env()
                self.client.ping()
            except Exception as exc:
                raise SandboxError(f"OCI/Docker daemon unavailable: {exc}") from exc

    def validate_runtime_available(self, runtime_name: str | None = None) -> str:
        """Verify host daemon registers the required kernel-isolation OCI runtime."""
        requested = runtime_name or self.isolation_runtime.value
        info = self.client.info()
        runtimes = info.get("Runtimes") if isinstance(info, dict) else {}
        available = set(runtimes.keys()) if isinstance(runtimes, dict) else set()
        if requested in available:
            return requested
        if self.allow_runc_fallback:
            logger.warning(
                "OCI runtime %r unavailable (available=%s); falling back to default runc",
                requested,
                sorted(available),
            )
            return "runc"
        raise SandboxError(
            f"Required kernel-isolation runtime {requested!r} is not registered on host daemon "
            f"(available={sorted(available)}). Refusing insecure downgrade to runc."
        )

    async def create(self, config: SandboxConfig) -> Sandbox:
        """Create a kernel-isolated gVisor/Kata container enforcing Restricted PSS."""
        mv_cfg = (
            config
            if isinstance(config, MicroVMSandboxConfig)
            else MicroVMSandboxConfig(
                image=config.image,
                cpu_limit=config.cpu_limit,
                memory_limit_mb=config.memory_limit_mb,
                disk_limit_mb=config.disk_limit_mb,
                max_pids=config.max_pids,
                timeout_seconds=config.timeout_seconds,
                network_enabled=config.network_enabled,
                network_mode=config.network_mode,
                isolation_runtime=self.isolation_runtime,
            )
        )
        resolved_runtime = self.validate_runtime_available(mv_cfg.isolation_runtime.value)
        security_opts: list[str] = []
        if mv_cfg.no_new_privileges:
            security_opts.append("no-new-privileges:true")
        if mv_cfg.seccomp_profile:
            security_opts.append(f"seccomp={mv_cfg.seccomp_profile}")

        net_mode = "none" if not mv_cfg.network_enabled else (mv_cfg.network_mode or "bridge")
        container_kwargs: dict[str, Any] = {
            "image": mv_cfg.image,
            "command": ["sleep", str(mv_cfg.timeout_seconds)],
            "detach": True,
            "runtime": resolved_runtime,
            "user": "1000:1000",
            "read_only": mv_cfg.read_only_rootfs,
            "cap_drop": list(mv_cfg.drop_capabilities),
            "security_opt": security_opts,
            "mem_limit": f"{mv_cfg.memory_limit_mb}m",
            "nano_cpus": int(mv_cfg.cpu_limit * 1_000_000_000),
            "pids_limit": mv_cfg.max_pids,
            "network_mode": net_mode,
            "tmpfs": {
                "/tmp": "rw,noexec,nosuid,size=128m",
                "/workspace": "rw,nosuid,size=512m",
            },
            "labels": {
                "pvs.managed": "true",
                "pvs.tenant_id": mv_cfg.tenant_id,
                "pvs.isolation_runtime": resolved_runtime,
            },
        }
        try:
            container = await asyncio.to_thread(self.client.containers.run, **container_kwargs)
        except Exception as exc:
            raise SandboxError(
                f"Failed to create {resolved_runtime} sandbox: {exc}"
            ) from exc

        return Sandbox(id=str(container.id), status=SandboxStatus.RUNNING, config=mv_cfg)

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        """Execute command inside the kernel-isolated sandbox with hard timeout."""
        timeout = timeout_seconds or sandbox.config.timeout_seconds
        start = time.perf_counter()
        try:
            container = await asyncio.to_thread(self.client.containers.get, sandbox.id)
            exec_res = await asyncio.wait_for(
                asyncio.to_thread(
                    container.exec_run,
                    ["/bin/sh", "-c", command],
                    user="1000:1000",
                    demux=True,
                ),
                timeout=float(timeout),
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Sandbox command timed out after {timeout}s in {sandbox.id}"
            ) from exc
        except Exception as exc:
            raise SandboxError(f"Sandbox execution failed in {sandbox.id}: {exc}") from exc

        elapsed = round(time.perf_counter() - start, 4)
        exit_code = int(exec_res.exit_code if exec_res.exit_code is not None else 1)
        out_pair = exec_res.output
        if isinstance(out_pair, tuple) and len(out_pair) == 2:
            raw_out, raw_err = out_pair
            stdout = raw_out.decode("utf-8", errors="replace") if raw_out else ""
            stderr = raw_err.decode("utf-8", errors="replace") if raw_err else ""
        else:
            stdout = out_pair.decode("utf-8", errors="replace") if out_pair else ""
            stderr = ""
        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=elapsed,
        )

    async def copy_in(self, sandbox: Sandbox, local_path: Path, sandbox_path: Path) -> None:
        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            tar.add(local_path, arcname=local_path.name)
        buf.seek(0)
        container = await asyncio.to_thread(self.client.containers.get, sandbox.id)
        await asyncio.to_thread(
            container.put_archive, sandbox_path.parent.as_posix(), buf.getvalue()
        )

    async def copy_out(self, sandbox: Sandbox, sandbox_path: Path, local_path: Path) -> None:
        container = await asyncio.to_thread(self.client.containers.get, sandbox.id)
        stream, _stat = await asyncio.to_thread(container.get_archive, sandbox_path.as_posix())
        raw = b"".join(stream)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _safe_extract_tar(raw, local_path.parent)

    async def destroy(self, sandbox: Sandbox) -> None:
        try:
            container = await asyncio.to_thread(self.client.containers.get, sandbox.id)
            await asyncio.to_thread(container.remove, force=True)
            sandbox.status = SandboxStatus.STOPPED
        except Exception as exc:
            raise SandboxError(f"Failed to destroy sandbox {sandbox.id}: {exc}") from exc


class E2BMicroVMSandboxProvider:
    """
    Firecracker MicroVM sandbox provider targeting the E2B Cloud/Self-Hosted REST API.

    Each tenant execution receives a dedicated hardware-virtualized Firecracker VM
    with zero shared host kernel attack surface.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = "https://api.e2b.dev",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        resolved_key = resolve_secret(
            vault_path="product-value-system/e2b",
            key="api_key",
            env_vars=("E2B_API_KEY",),
            explicit=api_key,
        )
        if not resolved_key:
            raise SandboxError("E2B_API_KEY is required for E2BMicroVMSandboxProvider")
        self.base_url = base_url.rstrip("/")
        client_kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "headers": {
                "X-API-Key": resolved_key,
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    async def create(self, config: SandboxConfig) -> Sandbox:
        mv_cfg = (
            config
            if isinstance(config, MicroVMSandboxConfig)
            else MicroVMSandboxConfig(
                image=config.image,
                cpu_limit=config.cpu_limit,
                memory_limit_mb=config.memory_limit_mb,
                timeout_seconds=config.timeout_seconds,
                network_enabled=config.network_enabled,
                isolation_runtime=KernelIsolationRuntime.E2B,
            )
        )
        payload = {
            "templateID": mv_cfg.e2b_template,
            "timeout": mv_cfg.timeout_seconds,
            "metadata": {
                "tenant_id": mv_cfg.tenant_id,
                "isolation_runtime": KernelIsolationRuntime.FIRECRACKER.value,
                "network_enabled": str(mv_cfg.network_enabled).lower(),
            },
        }
        try:
            resp = await self._client.post("/sandboxes", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise SandboxError(f"E2B microVM provisioning failed: {exc}") from exc

        sandbox_id = str(data.get("sandboxID") or data.get("id") or "")
        if not sandbox_id:
            raise SandboxError("E2B API response missing sandboxID")
        return Sandbox(id=sandbox_id, status=SandboxStatus.RUNNING, config=mv_cfg)

    async def execute(
        self,
        sandbox: Sandbox,
        command: str,
        timeout_seconds: int | None = None,
    ) -> ExecutionResult:
        timeout = timeout_seconds or sandbox.config.timeout_seconds
        start = time.perf_counter()
        try:
            resp = await self._client.post(
                f"/sandboxes/{sandbox.id}/commands",
                json={"cmd": command, "timeout": timeout},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"E2B microVM command timed out after {timeout}s") from exc
        except Exception as exc:
            raise SandboxError(f"E2B microVM execution failed: {exc}") from exc

        return ExecutionResult(
            exit_code=int(data.get("exitCode", data.get("exit_code", 0))),
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            duration_seconds=round(time.perf_counter() - start, 4),
        )

    async def copy_in(self, sandbox: Sandbox, local_path: Path, sandbox_path: Path) -> None:
        encoded = base64.b64encode(local_path.read_bytes()).decode("ascii")
        resp = await self._client.post(
            f"/sandboxes/{sandbox.id}/files",
            json={"path": sandbox_path.as_posix(), "content_base64": encoded},
        )
        resp.raise_for_status()

    async def copy_out(self, sandbox: Sandbox, sandbox_path: Path, local_path: Path) -> None:
        resp = await self._client.get(
            f"/sandboxes/{sandbox.id}/files",
            params={"path": sandbox_path.as_posix()},
        )
        resp.raise_for_status()
        raw = base64.b64decode(str(resp.json().get("content_base64", "")))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(raw)

    async def destroy(self, sandbox: Sandbox) -> None:
        try:
            resp = await self._client.delete(f"/sandboxes/{sandbox.id}")
            resp.raise_for_status()
            sandbox.status = SandboxStatus.STOPPED
        except Exception as exc:
            raise SandboxError(f"E2B microVM destroy failed for {sandbox.id}: {exc}") from exc

    async def close(self) -> None:
        await self._client.aclose()


def create_sandbox_provider(
    provider: str | None = None,
    **kwargs: Any,
) -> SandboxProvider:
    """
    Factory resolving ``SANDBOX_PROVIDER`` (``gvisor`` | ``kata`` | ``e2b`` | ``docker``).
    """
    selected = (provider or os.getenv("SANDBOX_PROVIDER") or "docker").strip().lower()
    if selected == "gvisor":
        return OCIHardenedRuntimeProvider(
            isolation_runtime=KernelIsolationRuntime.GVISOR,
            **kwargs,
        )
    if selected in {"kata", "kata-runtime"}:
        return OCIHardenedRuntimeProvider(
            isolation_runtime=KernelIsolationRuntime.KATA,
            **kwargs,
        )
    if selected in {"e2b", "firecracker"}:
        return E2BMicroVMSandboxProvider(**kwargs)
    if selected == "docker":
        from product_value_system.runtime.docker_sandbox import DockerSandboxProvider

        return DockerSandboxProvider(**kwargs)
    raise ValueError(
        f"Unknown sandbox provider {selected!r}; expected one of: docker, gvisor, kata, e2b"
    )

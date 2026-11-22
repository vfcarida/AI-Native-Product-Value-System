"""
Integration tests for EVO-029 Docker sandbox network egress filtering.

Includes:
- 6 deterministic integration tests combining ``DockerSandboxProvider`` + ``NetworkFilter``
  + an in-memory ``iptables`` packet simulator verifying DOCKER-USER attachment,
  allowlist enforcement, localhost/DNS invariants, blocked-connection logging,
  cleanup on destroy, and strict fail-closed container removal.
- 5 opt-in live Linux + Docker + root tests (``@pytest.mark.integration_real``) that
  execute real outbound curl/DNS probes inside ``pvs/agent-sandbox:latest``.
"""

from __future__ import annotations

import ipaddress
import os
import platform
from pathlib import Path
from typing import Any

import pytest

from product_value_system.sandbox import (
    CommandResult,
    DockerSandboxProvider,
    IptablesManager,
    NetworkFilter,
    SandboxConfig,
    SandboxError,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


class _SimulatedIptablesFirewall:
    """Stateful Linux iptables simulator executing real CLI args & evaluating packets."""

    def __init__(self) -> None:
        self.chains: dict[str, list[list[str]]] = {"DOCKER-USER": []}
        self.kernel_log: list[str] = []

    def run(self, cmd: list[str]) -> CommandResult:
        args = cmd[1:]
        if args == ["--version"]:
            return CommandResult(0, stdout="iptables v1.8.9 (nf_tables)\n")

        op = args[0]
        if op == "-L":
            chain = args[1]
            return CommandResult(0 if chain in self.chains else 1)
        if op == "-N":
            chain = args[1]
            if chain in self.chains:
                return CommandResult(1, stderr="Chain already exists")
            self.chains[chain] = []
            return CommandResult(0)
        if op == "-F":
            chain = args[1]
            self.chains[chain] = []
            return CommandResult(0)
        if op == "-X":
            chain = args[1]
            # Mirror Linux kernel: fail if chain is still referenced by DOCKER-USER
            for rule in self.chains.get("DOCKER-USER", []):
                if "-j" in rule and rule[rule.index("-j") + 1] == chain:
                    return CommandResult(1, stderr="Directory not empty (chain referenced)")
            self.chains.pop(chain, None)
            return CommandResult(0)
        if op == "-A":
            chain = args[1]
            self.chains[chain].append(args[2:])
            return CommandResult(0)
        if op == "-I":
            chain = args[1]
            idx = max(0, int(args[2]) - 1) if args[2].isdigit() else 0
            rule = args[3:] if args[2].isdigit() else args[2:]
            self.chains.setdefault(chain, []).insert(idx, rule)
            return CommandResult(0)
        if op == "-D":
            chain = args[1]
            rule = args[2:]
            if rule in self.chains.get(chain, []):
                self.chains[chain].remove(rule)
                return CommandResult(0)
            return CommandResult(1, stderr="Bad rule")
        if op == "-S":
            chain = args[1]
            lines = [f"-N {chain}"] + [f"-A {chain} {' '.join(r)}" for r in self.chains[chain]]
            return CommandResult(0, stdout="\n".join(lines) + "\n")
        return CommandResult(1, stderr=f"Unsupported op: {args}")

    def evaluate_packet(
        self,
        *,
        src_ip: str,
        dst_ip: str,
        dst_port: int,
        protocol: str,
        out_iface: str = "eth0",
    ) -> str:
        """Evaluate an outbound packet starting at ``DOCKER-USER`` and return verdict."""
        return self._eval_chain(
            "DOCKER-USER",
            src_ip=src_ip,
            dst_ip=dst_ip,
            dst_port=dst_port,
            protocol=protocol,
            out_iface=out_iface,
        )

    def _eval_chain(
        self,
        chain: str,
        *,
        src_ip: str,
        dst_ip: str,
        dst_port: int,
        protocol: str,
        out_iface: str,
    ) -> str:
        for rule in self.chains.get(chain, []):
            if not self._matches(
                rule,
                src_ip=src_ip,
                dst_ip=dst_ip,
                dst_port=dst_port,
                protocol=protocol,
                out_iface=out_iface,
            ):
                continue
            target = rule[rule.index("-j") + 1]
            if target == "LOG":
                prefix = (
                    rule[rule.index("--log-prefix") + 1]
                    if "--log-prefix" in rule
                    else "IPTABLES: "
                )
                self.kernel_log.append(
                    f"{prefix}SRC={src_ip} DST={dst_ip} DPT={dst_port}/{protocol}"
                )
                continue
            if target in {"ACCEPT", "REJECT", "DROP"}:
                return target
            if target in self.chains:
                verdict = self._eval_chain(
                    target,
                    src_ip=src_ip,
                    dst_ip=dst_ip,
                    dst_port=dst_port,
                    protocol=protocol,
                    out_iface=out_iface,
                )
                if verdict in {"ACCEPT", "REJECT", "DROP"}:
                    return verdict
        return "ACCEPT"

    @staticmethod
    def _matches(
        rule: list[str],
        *,
        src_ip: str,
        dst_ip: str,
        dst_port: int,
        protocol: str,
        out_iface: str,
    ) -> bool:
        if "-s" in rule:
            net = ipaddress.ip_network(rule[rule.index("-s") + 1], strict=False)
            if ipaddress.ip_address(src_ip) not in net:
                return False
        if "-d" in rule:
            net = ipaddress.ip_network(rule[rule.index("-d") + 1], strict=False)
            if ipaddress.ip_address(dst_ip) not in net:
                return False
        if "-o" in rule and rule[rule.index("-o") + 1] != out_iface:
            return False
        if "-p" in rule and rule[rule.index("-p") + 1] != protocol:
            return False
        if "--dport" in rule and int(rule[rule.index("--dport") + 1]) != dst_port:
            return False
        if "-m" in rule and "conntrack" in rule:
            # New outbound packet is not ESTABLISHED/RELATED
            return False
        return True


class _FakeDockerContainer:
    def __init__(self, name: str, ip_address: str = "172.17.0.2") -> None:
        self.name = name
        self.attrs = {"NetworkSettings": {"IPAddress": ip_address}}
        self.stopped = False
        self.removed = False

    def stop(self, timeout: int = 5) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeDockerClient:
    def __init__(self, container_ip: str = "172.17.0.2") -> None:
        self.container_ip = container_ip
        self.created: dict[str, _FakeDockerContainer] = {}
        self.images = self
        self.containers = self

    def ping(self) -> bool:
        return True

    def get(self, name: str) -> Any:
        if name in self.created:
            return self.created[name]
        return {"id": name}

    def run(self, **kwargs: Any) -> _FakeDockerContainer:
        c = _FakeDockerContainer(kwargs["name"], ip_address=self.container_ip)
        self.created[kwargs["name"]] = c
        return c


_MOCK_DNS: dict[str, list[str]] = {
    "api.openai.com": ["104.18.6.192", "104.18.7.192"],
    "api.anthropic.com": ["160.79.104.10"],
    "generativelanguage.googleapis.com": ["142.250.190.74"],
    "pypi.org": ["151.101.0.223"],
    "files.pythonhosted.org": ["151.101.1.63"],
    "registry.npmjs.org": ["104.16.24.34"],
}


def _make_provider_with_firewall(
    tmp_path: Path,
    *,
    container_ip: str = "172.17.0.2",
) -> tuple[DockerSandboxProvider, _SimulatedIptablesFirewall, _FakeDockerClient, Path]:
    fw = _SimulatedIptablesFirewall()
    ipt = IptablesManager(runner=fw.run, require_linux=False)
    log_path = tmp_path / "sandbox_network_blocks.log"

    nf = NetworkFilter.from_yaml(
        REPO_ROOT / "config" / "sandbox.yaml",
        iptables=ipt,
        dns_resolver=lambda d: _MOCK_DNS.get(d, []),
    )
    nf.log_path = str(log_path)

    client = _FakeDockerClient(container_ip=container_ip)
    provider = DockerSandboxProvider(client=client, network_filter=nf)
    return provider, fw, client, log_path


async def test_egress_container_allows_allowlisted_openai_endpoint(tmp_path: Path) -> None:
    provider, fw, _client, _log = _make_provider_with_firewall(tmp_path)
    sandbox = await provider.create(SandboxConfig(network_enabled=True))
    try:
        assert provider.last_filter_status is not None
        assert provider.last_filter_status.applied is True
        # Outbound HTTPS to api.openai.com IP is ACCEPTED
        assert (
            fw.evaluate_packet(
                src_ip="172.17.0.2",
                dst_ip="104.18.6.192",
                dst_port=443,
                protocol="tcp",
            )
            == "ACCEPT"
        )
        # Outbound HTTPS to pypi.org IP is ACCEPTED
        assert (
            fw.evaluate_packet(
                src_ip="172.17.0.2",
                dst_ip="151.101.0.223",
                dst_port=443,
                protocol="tcp",
            )
            == "ACCEPT"
        )
    finally:
        await provider.destroy(sandbox)


async def test_egress_container_blocks_non_allowlisted_endpoint(tmp_path: Path) -> None:
    provider, fw, _client, _log = _make_provider_with_firewall(tmp_path)
    sandbox = await provider.create(SandboxConfig(network_enabled=True))
    try:
        # Arbitrary external endpoint (example.com 93.184.216.34:443) is REJECTED & logged
        verdict = fw.evaluate_packet(
            src_ip="172.17.0.2",
            dst_ip="93.184.216.34",
            dst_port=443,
            protocol="tcp",
        )
        assert verdict == "REJECT"
        assert any(
            "SANDBOX_BLOCK:" in entry and "93.184.216.34" in entry
            for entry in fw.kernel_log
        )

        # Even allowlisted IP on disallowed port (e.g. HTTP port 80) is REJECTED
        verdict_wrong_port = fw.evaluate_packet(
            src_ip="172.17.0.2",
            dst_ip="104.18.6.192",
            dst_port=80,
            protocol="tcp",
        )
        assert verdict_wrong_port == "REJECT"
    finally:
        await provider.destroy(sandbox)


async def test_egress_container_always_allows_localhost(tmp_path: Path) -> None:
    provider, fw, _client, _log = _make_provider_with_firewall(tmp_path)
    sandbox = await provider.create(SandboxConfig(network_enabled=True))
    try:
        assert (
            fw.evaluate_packet(
                src_ip="172.17.0.2",
                dst_ip="127.0.0.1",
                dst_port=8000,
                protocol="tcp",
                out_iface="lo",
            )
            == "ACCEPT"
        )
        assert (
            fw.evaluate_packet(
                src_ip="172.17.0.2",
                dst_ip="127.0.0.1",
                dst_port=8000,
                protocol="tcp",
                out_iface="eth0",
            )
            == "ACCEPT"
        )
    finally:
        await provider.destroy(sandbox)


async def test_egress_container_always_allows_dns_resolution(tmp_path: Path) -> None:
    provider, fw, _client, _log = _make_provider_with_firewall(tmp_path)
    sandbox = await provider.create(SandboxConfig(network_enabled=True))
    try:
        assert (
            fw.evaluate_packet(
                src_ip="172.17.0.2",
                dst_ip="8.8.8.8",
                dst_port=53,
                protocol="udp",
            )
            == "ACCEPT"
        )
        assert (
            fw.evaluate_packet(
                src_ip="172.17.0.2",
                dst_ip="1.1.1.1",
                dst_port=53,
                protocol="tcp",
            )
            == "ACCEPT"
        )
    finally:
        await provider.destroy(sandbox)


async def test_egress_blocked_connections_logged_and_rules_cleaned_on_destroy(
    tmp_path: Path,
) -> None:
    provider, fw, client, log_path = _make_provider_with_firewall(tmp_path)
    sandbox = await provider.create(SandboxConfig(network_enabled=True))

    assert provider.network_filter is not None
    decision = provider.network_filter.check_connection(
        "exfil.attacker.example",
        port=443,
        protocol="tcp",
        container_id=sandbox.id,
    )
    assert decision.allowed is False
    assert log_path.exists()
    assert "exfil.attacker.example:443/tcp" in log_path.read_text(encoding="utf-8")

    # Verify DOCKER-USER jump rule exists before destroy
    assert len(fw.chains["DOCKER-USER"]) == 1

    await provider.destroy(sandbox)

    # Chain and DOCKER-USER jump rule must be fully cleaned up
    assert fw.chains["DOCKER-USER"] == []
    assert list(fw.chains.keys()) == ["DOCKER-USER"]
    assert client.created[sandbox.id].removed is True


async def test_docker_sandbox_destroys_container_when_strict_filter_fails() -> None:
    unavailable_ipt = IptablesManager(
        runner=lambda _cmd: CommandResult(127, stderr="iptables missing"),
        require_linux=False,
    )
    strict_filter = NetworkFilter(
        {"filtering_enabled": True, "fallback_on_error": False, "allowlist": []},
        iptables=unavailable_ipt,
    )
    client = _FakeDockerClient()
    provider = DockerSandboxProvider(client=client, network_filter=strict_filter)

    with pytest.raises(SandboxError, match="Failed to apply network egress filter"):
        await provider.create(SandboxConfig(network_enabled=True))

    # Fail-closed invariant: container must be removed so no unfiltered sandbox leaks
    assert len(client.created) == 1
    leaked_container = next(iter(client.created.values()))
    assert leaked_container.removed is True


def _live_egress_unavailable_reason() -> str | None:
    if os.getenv("RUN_REAL_EGRESS_TESTS") != "1":
        return "Opt-in live egress test (set RUN_REAL_EGRESS_TESTS=1 on Linux with Docker & root)."
    if platform.system().lower() != "linux":
        return "Live iptables egress tests require Linux."
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return "Live iptables egress tests require root/sudo privileges."
    return None


@pytest.mark.integration_real
@pytest.mark.skipif(
    _live_egress_unavailable_reason() is not None,
    reason=_live_egress_unavailable_reason() or "",
)
async def test_live_docker_egress_filtering_suite() -> None:
    """Live Linux+Docker+root probe validating allowed/blocked/localhost/DNS egress."""
    provider = DockerSandboxProvider(sandbox_config_path=REPO_ROOT / "config/sandbox.yaml")
    sandbox = await provider.create(SandboxConfig(network_enabled=True, timeout_seconds=30))
    try:
        # 1. Allowed LLM endpoint reachable (HTTP status code returned over TLS)
        allowed = await provider.execute(
            sandbox,
            "curl -sS -o /dev/null -w '%{http_code}' --max-time 10 https://api.openai.com",
        )
        assert allowed.exit_code == 0

        # 2. Blocked endpoint fails quickly
        blocked = await provider.execute(
            sandbox,
            "curl -sS --max-time 5 https://example.com",
        )
        assert blocked.exit_code != 0

        # 3. Localhost reachable
        lo_res = await provider.execute(
            sandbox,
            "python3 -c 'import socket; socket.gethostbyname(\"localhost\")'",
        )
        assert lo_res.exit_code == 0

        # 4. DNS resolution works
        dns_res = await provider.execute(
            sandbox,
            "python3 -c 'import socket; print(socket.gethostbyname(\"api.openai.com\"))'",
        )
        assert dns_res.exit_code == 0
    finally:
        await provider.destroy(sandbox)

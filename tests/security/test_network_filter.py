"""
Security & performance test suite for PROD-003: iptables Network Egress Filtering
for Docker Sandboxes.

Covers:
- NetworkRule validation & config/network_allowlist.yaml loading (20+ approved endpoints)
- Default-deny iptables OUTPUT rule generation (flush, DROP, loopback, ESTABLISHED, DNS, LOG)
- IPv4/CIDR, domain resolution with 1-hour TTL cache, and wildcard subdomain support
- Dynamic allowlist reload without container restarts
- Shell injection rejection in iptables command validation
- DockerSandbox & apply_rules_in_container integration (allowed/blocked/fallback/cleanup)
- Sandbox Dockerfiles (iptables installed + non-root UID 1000)
- Performance target (< 5ms rule generation & in-container application overhead)
- Optional live Docker integration tests (auto-skipped when Docker daemon is unavailable)
"""

from __future__ import annotations

import socket
import statistics
import time
from pathlib import Path
from typing import Any

import pytest

from product_value_system.sandbox import (
    DockerSandbox,
    NetworkFilter,
    NetworkRule,
    load_allowlist_from_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_YAML = REPO_ROOT / "config" / "network_allowlist.yaml"


class _FakeDockerContainer:
    """In-memory mock Docker container enforcing applied iptables rules on simulated connects."""

    def __init__(self, *, fail_iptables: bool = False) -> None:
        self.fail_iptables = fail_iptables
        self.started = False
        self.stopped = False
        self.removed = False
        self.applied_commands: list[list[str]] = []
        self.exec_calls: list[tuple[Any, dict[str, Any]]] = []
        self.default_drop = False
        self.allowed_targets: set[tuple[str, str, int]] = set()
        self.loopback_allowed = False
        self.dns_allowed = False

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: int = 1) -> None:
        self.stopped = True

    def remove(self, force: bool = False) -> None:
        self.removed = True

    def exec_run(self, cmd: Any, **kwargs: Any) -> tuple[int, bytes]:
        self.exec_calls.append((cmd, dict(kwargs)))
        tokens = cmd if isinstance(cmd, list) else str(cmd).split()

        if tokens and tokens[0] == "iptables":
            if self.fail_iptables:
                return 1, b"iptables: Permission denied (you must be root)"
            self.applied_commands.append(list(tokens))
            if "-P" in tokens and "OUTPUT" in tokens and "DROP" in tokens:
                self.default_drop = True
            if "-o" in tokens and "lo" in tokens and "ACCEPT" in tokens:
                self.loopback_allowed = True
            if "--dport" in tokens and "53" in tokens and "ACCEPT" in tokens:
                self.dns_allowed = True
            if "-d" in tokens and "ACCEPT" in tokens:
                dest_ip = tokens[tokens.index("-d") + 1]
                proto = tokens[tokens.index("-p") + 1] if "-p" in tokens else "any"
                port = int(tokens[tokens.index("--dport") + 1]) if "--dport" in tokens else 0
                self.allowed_targets.add((dest_ip, proto, port))
            return 0, b""

        # Simulated code execution verifying allowlist enforcement
        code = tokens[-1] if tokens else ""
        if "127.0.0.1" in code:
            allowed_lo = not self.default_drop or self.loopback_allowed
            return (0, b"OK\n") if allowed_lo else (1, b"DENY")
        if "gethostbyname" in code:
            return (0, b"142.250.190.46\n") if (not self.default_drop or self.dns_allowed) else (
                1,
                b"DNS timeout",
            )
        if "api.openai.com" in code or "104.18.6.192" in code:
            if not self.default_drop or ("104.18.6.192", "tcp", 443) in self.allowed_targets:
                return 0, b"200\n"
            return 1, b"Connection timed out"
        if "evil.com" in code or "198.51.100.99" in code:
            if not self.default_drop or ("198.51.100.99", "tcp", 443) in self.allowed_targets:
                return 0, b"200\n"
            return 1, b"ConnectionError: blocked by iptables timeout"
        return 0, b"executed\n"


class _FakeDockerClient:
    def __init__(self, *, fail_iptables: bool = False) -> None:
        self.fail_iptables = fail_iptables
        self.created_kwargs: list[dict[str, Any]] = []
        self.last_container: _FakeDockerContainer | None = None

    class _ContainersAPI:
        def __init__(self, outer: _FakeDockerClient) -> None:
            self._outer = outer

        def create(self, **kwargs: Any) -> _FakeDockerContainer:
            self._outer.created_kwargs.append(kwargs)
            container = _FakeDockerContainer(fail_iptables=self._outer.fail_iptables)
            self._outer.last_container = container
            return container

    @property
    def containers(self) -> _ContainersAPI:
        return self._ContainersAPI(self)


def _fake_dns(domain: str) -> list[str]:
    table = {
        "api.openai.com": ["104.18.6.192", "104.18.7.192"],
        "api.anthropic.com": ["160.79.104.10"],
        "generativelanguage.googleapis.com": ["142.250.190.10"],
        "bedrock-runtime.us-east-1.amazonaws.com": ["52.94.236.248"],
        "bedrock-runtime.us-west-2.amazonaws.com": ["52.94.236.249"],
        "bedrock-runtime.eu-west-1.amazonaws.com": ["52.94.236.250"],
        "myorg.openai.azure.com": ["20.119.16.40"],
        "httpbin.org": ["34.232.165.90"],
        "pypi.org": ["151.101.0.223"],
        "files.pythonhosted.org": ["151.101.1.63"],
        "registry.npmjs.org": ["104.16.25.34"],
        "github.com": ["140.82.121.4"],
        "api.github.com": ["140.82.121.6"],
    }
    if domain in table:
        return table[domain]
    raise socket.gaierror(f"Name or service not known: {domain}")


@pytest.mark.security
def test_allowlist_yaml_validation_has_20_plus_approved_endpoints() -> None:
    """config/network_allowlist.yaml loads >= 20 validated endpoints across AI, registries, DNS."""
    assert ALLOWLIST_YAML.exists()
    rules = load_allowlist_from_config(ALLOWLIST_YAML)
    assert len(rules) >= 20

    destinations = {r.destination for r in rules}
    for required in (
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "bedrock-runtime.us-east-1.amazonaws.com",
        "*.openai.azure.com",
        "pypi.org",
        "registry.npmjs.org",
        "github.com",
        "8.8.8.8",
        "1.1.1.1",
        "127.0.0.1",
    ):
        assert required in destinations, f"Missing required allowlist destination: {required}"

    for rule in rules:
        assert rule.destination
        assert rule.protocol in {"tcp", "udp", "icmp", "any"}
        if rule.port != "any":
            assert 1 <= int(rule.port) <= 65535


@pytest.mark.security
def test_network_rule_rejects_invalid_and_hostile_inputs() -> None:
    """NetworkRule rejects shell injection, invalid ports, invalid protocols, and empty hosts."""
    with pytest.raises(ValueError):
        NetworkRule("")
    with pytest.raises(ValueError, match="Unsafe"):
        NetworkRule("api.openai.com; rm -rf /", 443, "tcp")
    with pytest.raises(ValueError, match="Invalid destination"):
        NetworkRule("bad_host_!!!", 443, "tcp")
    with pytest.raises(ValueError, match="Port must be"):
        NetworkRule("api.openai.com", 70000, "tcp")
    with pytest.raises(ValueError, match="Invalid protocol"):
        NetworkRule("api.openai.com", 443, "sctp")


@pytest.mark.security
def test_network_filter_generates_default_deny_iptables_rules() -> None:
    """NetworkFilter generates clean-slate default-deny OUTPUT iptables chain with LOG."""
    nf = NetworkFilter(
        [
            NetworkRule("httpbin.org", 443, "tcp", "Test endpoint"),
            NetworkRule("10.20.0.0/16", 443, "tcp", "Internal CIDR"),
        ],
        dns_resolver=_fake_dns,
    )
    commands = nf.generate_iptables_rules()

    assert commands[0] == "iptables -F OUTPUT"
    assert commands[1] == "iptables -P OUTPUT DROP"
    assert "iptables -A OUTPUT -o lo -j ACCEPT" in commands
    assert "iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT" in commands
    assert "iptables -A OUTPUT -p udp --dport 53 -j ACCEPT" in commands
    assert "iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT" in commands
    assert "iptables -A OUTPUT -p tcp -d 34.232.165.90 --dport 443 -j ACCEPT" in commands
    assert "iptables -A OUTPUT -p tcp -d 10.20.0.0/16 --dport 443 -j ACCEPT" in commands
    assert any("SANDBOX-DENIED:" in cmd and "-j LOG" in cmd for cmd in commands)


@pytest.mark.security
def test_wildcard_subdomain_resolution_and_safe_skip() -> None:
    """Wildcard rules (*.openai.azure.com) resolve pre-configured subdomains or skip safely."""
    rule = NetworkRule("*.openai.azure.com", 443, "tcp", "Azure OpenAI")
    assert rule.is_wildcard is True

    # Without pre-configured subdomains: does not crash or emit invalid literal wildcard IP
    nf_plain = NetworkFilter([rule], dns_resolver=_fake_dns)
    cmds_plain = nf_plain.generate_iptables_rules()
    assert not any("*.openai.azure.com" in c for c in cmds_plain)

    # With pre-configured tenant subdomains: emits resolved IPv4 ACCEPT rule
    nf_sub = NetworkFilter(
        [rule],
        dns_resolver=_fake_dns,
        wildcard_subdomains={"*.openai.azure.com": ["myorg.openai.azure.com"]},
    )
    cmds_sub = nf_sub.generate_iptables_rules()
    assert "iptables -A OUTPUT -p tcp -d 20.119.16.40 --dport 443 -j ACCEPT" in cmds_sub


@pytest.mark.security
def test_dns_resolution_ttl_caching_and_force_refresh() -> None:
    """Resolved domain IPs are cached for 1-hour TTL and refreshed on force_refresh."""
    calls = {"count": 0}

    def counting_dns(domain: str) -> list[str]:
        calls["count"] += 1
        return ["104.18.6.192"]

    nf = NetworkFilter(
        [NetworkRule("api.openai.com", 443, "tcp")],
        dns_resolver=counting_dns,
    )
    assert nf.dns_cache_ttl_seconds == pytest.approx(3600.0)

    ips1 = nf._resolve_domain("api.openai.com")
    ips2 = nf._resolve_domain("api.openai.com")
    assert ips1 == {"104.18.6.192"}
    assert ips2 == {"104.18.6.192"}
    assert calls["count"] == 1

    nf._resolve_domain("api.openai.com", force_refresh=True)
    assert calls["count"] == 2


@pytest.mark.security
def test_dynamic_allowlist_reload_without_restart(tmp_path: Path) -> None:
    """reload_allowlist updates rules and clears DNS cache without rebuilding containers."""
    cfg = tmp_path / "allowlist.yaml"
    cfg.write_text(
        'version: "1.0"\nallowlist:\n  - destination: "api.openai.com"\n    port: 443\n',
        encoding="utf-8",
    )
    nf = NetworkFilter(load_allowlist_from_config(cfg), dns_resolver=_fake_dns)
    cmds_before = nf.generate_iptables_rules()
    assert any("104.18.6.192" in c for c in cmds_before)
    assert not any("160.79.104.10" in c for c in cmds_before)

    # Update YAML on disk and dynamically reload
    cfg.write_text(
        'version: "1.0"\nallowlist:\n'
        '  - destination: "api.openai.com"\n    port: 443\n'
        '  - destination: "api.anthropic.com"\n    port: 443\n',
        encoding="utf-8",
    )
    count = nf.reload_allowlist(cfg)
    assert count == 2
    cmds_after = nf.generate_iptables_rules()
    assert any("160.79.104.10" in c for c in cmds_after)


@pytest.mark.security
def test_apply_rules_in_container_rejects_shell_injection() -> None:
    """apply_rules_in_container rejects non-iptables commands and shell metacharacters."""
    container = _FakeDockerContainer()
    with pytest.raises(ValueError, match="non-iptables"):
        NetworkFilter.apply_rules_in_container(container, ["rm -rf /"])
    with pytest.raises(ValueError, match="metacharacter"):
        NetworkFilter.apply_rules_in_container(
            container, ["iptables -F OUTPUT; curl https://evil.com"]
        )
    assert container.applied_commands == []


@pytest.mark.security
def test_apply_rules_in_container_strict_vs_graceful_fallback() -> None:
    """Failed iptables raises RuntimeError in strict mode and degrades safely with fallback."""
    container = _FakeDockerContainer(fail_iptables=True)
    cmds = ["iptables -F OUTPUT", "iptables -P OUTPUT DROP"]

    with pytest.raises(RuntimeError, match="iptables command failed"):
        NetworkFilter.apply_rules_in_container(container, cmds, fallback_on_error=False)

    latency_ms = NetworkFilter.apply_rules_in_container(container, cmds, fallback_on_error=True)
    assert latency_ms >= 0.0


@pytest.mark.security
def test_docker_sandbox_enforces_allow_block_dns_and_loopback() -> None:
    """DockerSandbox grants NET_ADMIN only for setup, blocks evil.com, allows OpenAI/DNS/lo."""
    fake_client = _FakeDockerClient()
    sandbox = DockerSandbox(
        enable_network_filter=True,
        allowlist_path=ALLOWLIST_YAML,
        client=fake_client,
        dns_resolver=_fake_dns,
    )

    # 1. Allowed destination (api.openai.com) succeeds
    ok_res = sandbox.execute_code(
        'import requests; print(requests.get("https://api.openai.com/v1/models").status_code)',
        language="python",
    )
    assert ok_res.exit_code == 0
    assert "200" in ok_res.stdout
    assert fake_client.last_container is not None
    assert fake_client.last_container.stopped is True
    assert fake_client.last_container.removed is True
    assert fake_client.created_kwargs[-1]["cap_add"] == ["NET_ADMIN"]
    assert fake_client.created_kwargs[-1]["cap_drop"] == ["ALL"]
    assert fake_client.created_kwargs[-1]["user"] == "1000:1000"
    assert sandbox.last_filter_latency_ms is not None
    assert sandbox.last_filter_latency_ms < 5.0

    # 2. Blocked destination (evil.com) fails
    blocked_res = sandbox.execute_code(
        'import requests; requests.get("https://evil.com", timeout=2)',
        language="python",
    )
    assert blocked_res.exit_code != 0
    assert "blocked" in blocked_res.stderr.lower() or "timeout" in blocked_res.stderr.lower()

    # 3. DNS resolution (port 53) allowed even with default-deny
    dns_res = sandbox.execute_code(
        'import socket; print(socket.gethostbyname("google.com"))',
        language="python",
    )
    assert dns_res.exit_code == 0
    assert any(ch.isdigit() for ch in dns_res.stdout)

    # 4. Loopback (127.0.0.1) always allowed
    lo_res = sandbox.execute_code(
        'import socket; s = socket.socket(); s.bind(("127.0.0.1", 8888)); print("OK")',
        language="python",
    )
    assert lo_res.exit_code == 0
    assert "OK" in lo_res.stdout


@pytest.mark.security
def test_docker_sandbox_disabled_mode_and_env_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Feature flag disables filtering cleanly and omits NET_ADMIN capability."""
    fake_client = _FakeDockerClient()
    monkeypatch.setenv("SANDBOX_NETWORK_FILTER_ENABLED", "false")
    sandbox = DockerSandbox(client=fake_client)
    assert sandbox.enable_network_filter is False
    assert sandbox.network_filter is None

    res = sandbox.execute_code('print("unfiltered")', language="python")
    assert res.exit_code == 0
    assert "cap_add" not in fake_client.created_kwargs[-1]


@pytest.mark.security
def test_docker_sandbox_rejects_unknown_language() -> None:
    """DockerSandbox validates language before spawning containers."""
    sandbox = DockerSandbox(enable_network_filter=False, client=_FakeDockerClient())
    with pytest.raises(ValueError, match="Unsupported sandbox language"):
        sandbox.execute_code("echo hi", language="ruby")


@pytest.mark.security
def test_sandbox_dockerfiles_install_iptables_and_run_non_root() -> None:
    """All sandbox Dockerfiles install iptables and switch to a non-root UID 1000 user."""
    dockerfiles = [
        REPO_ROOT / "docker" / "agent-sandbox" / "Dockerfile",
        REPO_ROOT / "deploy" / "docker" / "Dockerfile.sandbox-python",
        REPO_ROOT / "deploy" / "docker" / "Dockerfile.sandbox-typescript",
    ]
    for df_path in dockerfiles:
        assert df_path.exists(), f"Missing sandbox Dockerfile: {df_path}"
        content = df_path.read_text(encoding="utf-8")
        assert "iptables" in content, f"{df_path.name} must install iptables"
        assert "USER " in content and "USER root" not in content, (
            f"{df_path.name} must run as non-root user"
        )
        assert "1000" in content, f"{df_path.name} must provision UID 1000 user"


@pytest.mark.security
@pytest.mark.parametrize(
    "destination,port,should_pass",
    [
        ("api.openai.com", 443, True),
        ("api.anthropic.com", 443, True),
        ("generativelanguage.googleapis.com", 443, True),
        ("evil.com", 443, False),
        ("192.168.1.1", 22, False),
        ("169.254.169.254", 80, False),
    ],
)
def test_allowlist_policy_enforcement_parametrized(
    destination: str,
    port: int,
    should_pass: bool,
) -> None:
    """Parametrized allowlist policy allows approved APIs and blocks evil/RFC1918/IMDS targets."""
    rules = load_allowlist_from_config(ALLOWLIST_YAML)
    nf = NetworkFilter(rules, dns_resolver=_fake_dns)
    decision = nf.check_connection(destination, port=port, protocol="tcp")
    assert decision.allowed is should_pass, (
        f"Expected {destination}:{port} allowed={should_pass}, got {decision}"
    )


@pytest.mark.security
def test_performance_overhead_under_5ms(benchmark: Any) -> None:
    """NetworkFilter rule generation & in-container application completes in < 5ms p50."""
    rules = load_allowlist_from_config(ALLOWLIST_YAML)
    nf = NetworkFilter(rules, dns_resolver=_fake_dns)
    # Warm DNS cache (production 1-hour TTL cache steady state)
    _ = nf.generate_iptables_rules()

    def _apply() -> float:
        container = _FakeDockerContainer()
        commands = nf.generate_iptables_rules()
        return NetworkFilter.apply_rules_in_container(container, commands)

    benchmark(_apply)

    samples_ms: list[float] = []
    for _ in range(30):
        t0 = time.perf_counter()
        _apply()
        samples_ms.append((time.perf_counter() - t0) * 1000.0)

    p50_ms = statistics.median(samples_ms)
    assert p50_ms < 5.0, f"Filter setup p50 latency {p50_ms:.2f}ms exceeded 5ms target"


def _docker_sandbox_image_available() -> bool:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        client.images.get("sandbox-python:latest")
        return True
    except Exception:
        return False


@pytest.mark.integration_real
@pytest.mark.security
def test_live_docker_sandbox_allowed_and_blocked_connections() -> None:
    """Live Docker integration: allows api.openai.com and blocks evil.com (requires daemon)."""
    if not _docker_sandbox_image_available():
        pytest.skip("Docker daemon or sandbox-python:latest image not available")

    sandbox = DockerSandbox(enable_network_filter=True, allowlist_path=ALLOWLIST_YAML)
    ok_res = sandbox.execute_code(
        'import requests; print(requests.get("https://api.openai.com/v1/models", '
        "timeout=5).status_code)",
        language="python",
    )
    assert ok_res.exit_code == 0
    assert "200" in ok_res.stdout or "401" in ok_res.stdout

    blocked_res = sandbox.execute_code(
        'import requests; requests.get("https://evil.com", timeout=2)',
        language="python",
    )
    assert blocked_res.exit_code != 0

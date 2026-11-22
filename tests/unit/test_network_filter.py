"""Unit tests for EVO-029 NetworkFilter, AllowlistEntry, and IptablesManager."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from product_value_system.sandbox import (
    AllowlistEntry,
    CommandResult,
    IptablesError,
    IptablesManager,
    NetworkFilter,
    load_sandbox_network_config,
    sanitize_chain_name,
)
from product_value_system.sandbox.__main__ import main as sandbox_cli_main
from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ok_runner_factory() -> tuple[list[list[str]], object]:
    recorded: list[list[str]] = []
    existing_chains: set[str] = set()

    def runner(cmd: list[str]) -> CommandResult:
        recorded.append(list(cmd))
        if "--version" in cmd:
            return CommandResult(0, stdout="iptables v1.8.9 (nf_tables)\n")
        if "-L" in cmd:
            chain = cmd[cmd.index("-L") + 1]
            return CommandResult(0 if chain in existing_chains else 1)
        if "-N" in cmd:
            existing_chains.add(cmd[cmd.index("-N") + 1])
            return CommandResult(0)
        if "-X" in cmd:
            existing_chains.discard(cmd[cmd.index("-X") + 1])
            return CommandResult(0)
        return CommandResult(0)

    return recorded, runner


def test_parse_allowlist_from_yaml_config() -> None:
    cfg_path = REPO_ROOT / "config" / "sandbox.yaml"
    assert cfg_path.exists()
    raw = load_sandbox_network_config(cfg_path)
    assert raw["filtering_enabled"] is True
    assert raw["mode"] == "allowlist"
    assert raw["fallback_on_error"] is True

    nf = NetworkFilter.from_yaml(cfg_path)
    assert nf.enabled is True
    assert nf.mode == "allowlist"
    assert nf.log_blocked_connections is True
    domains = {e.domain for e in nf.allowlist if e.domain}
    ips = {e.ip for e in nf.allowlist if e.ip}
    assert {"api.openai.com", "api.anthropic.com", "pypi.org", "registry.npmjs.org"} <= domains
    assert {"8.8.8.8", "1.1.1.1", "127.0.0.1"} <= ips


def test_allowlist_entry_resolves_domain_to_ipv4() -> None:
    entry = AllowlistEntry(domain="api.openai.com", port=443, protocol="tcp")
    ips = entry.resolve_ips(
        resolver=lambda d: ["104.18.7.192", "2606:4700::6812:7c0", "104.18.6.192"]
    )
    assert ips == ["104.18.6.192", "104.18.7.192"]

    def failing_dns(_domain: str) -> list[str]:
        raise socket.gaierror("temporary failure in name resolution")

    assert entry.resolve_ips(resolver=failing_dns) == []


def test_allowlist_entry_with_ip_and_cidr_validation() -> None:
    called = {"dns": 0}

    def unexpected_dns(_domain: str) -> list[str]:
        called["dns"] += 1
        return []

    dns_entry = AllowlistEntry(ip="8.8.8.8", port=53, protocol="udp")
    assert dns_entry.resolve_ips(resolver=unexpected_dns) == ["8.8.8.8"]

    cidr_entry = AllowlistEntry(ip="10.0.0.0/8", port="any", protocol="any")
    assert cidr_entry.resolve_ips(resolver=unexpected_dns) == ["10.0.0.0/8"]
    assert called["dns"] == 0

    with pytest.raises(ValueError, match="domain"):
        AllowlistEntry()
    with pytest.raises(ValueError, match="Invalid IP"):
        AllowlistEntry(ip="not-an-ip")
    with pytest.raises(ValueError, match="Invalid domain"):
        AllowlistEntry(domain="bad domain; rm -rf /")
    with pytest.raises(ValueError, match="Port must be"):
        AllowlistEntry(ip="8.8.8.8", port=70000)
    with pytest.raises(ValueError, match="Invalid protocol"):
        AllowlistEntry(ip="8.8.8.8", protocol="sctp")


def test_generate_iptables_rule_format_allowlist_and_denylist() -> None:
    entries = [
        AllowlistEntry(domain="api.openai.com", port=443, protocol="tcp"),
        AllowlistEntry(ip="8.8.8.8", port=53, protocol="udp"),
        AllowlistEntry(ip="127.0.0.1", port="any", protocol="any"),
    ]
    resolved = [
        (entries[0], ["104.18.6.192"]),
        (entries[1], ["8.8.8.8"]),
        (entries[2], ["127.0.0.1"]),
    ]

    allow_filter = NetworkFilter(
        {
            "filtering_enabled": True,
            "mode": "allowlist",
            "allowlist": entries,
            "log_blocked_connections": True,
        }
    )
    rules = allow_filter.generate_rules(resolved)
    assert rules[0] == "-o lo -j ACCEPT"
    assert "-p udp --dport 53 -j ACCEPT" in rules
    assert "-p tcp --dport 53 -j ACCEPT" in rules
    assert "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT" in rules
    assert "-d 104.18.6.192 -p tcp --dport 443 -j ACCEPT" in rules
    assert "-d 8.8.8.8 -p udp --dport 53 -j ACCEPT" in rules
    assert "-d 127.0.0.1 -j ACCEPT" in rules
    assert rules[-2] == '-j LOG --log-prefix "SANDBOX_BLOCK: " --log-level 4'
    assert rules[-1] == "-j REJECT --reject-with icmp-port-unreachable"

    deny_filter = NetworkFilter(
        {
            "filtering_enabled": True,
            "mode": "denylist",
            "allowlist": [AllowlistEntry(ip="203.0.113.5", port=443, protocol="tcp")],
            "log_blocked_connections": False,
        }
    )
    deny_rules = deny_filter.generate_rules([(deny_filter.allowlist[0], ["203.0.113.5"])])
    assert "-d 203.0.113.5 -p tcp --dport 443 -j REJECT --reject-with icmp-port-unreachable" in (
        deny_rules
    )
    assert deny_rules[-1] == "-j ACCEPT"


def test_iptables_availability_check_linux_and_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    _, runner = _ok_runner_factory()
    mgr = IptablesManager(runner=runner, require_linux=True)

    monkeypatch.setattr(
        "product_value_system.sandbox.iptables_manager.platform.system",
        lambda: "Windows",
    )
    assert mgr.is_available() is False

    monkeypatch.setattr(
        "product_value_system.sandbox.iptables_manager.platform.system",
        lambda: "Linux",
    )
    assert mgr.is_available() is True

    missing_mgr = IptablesManager(
        runner=lambda _cmd: CommandResult(127, stderr="not found"),
        require_linux=False,
    )
    assert missing_mgr.is_available() is False


def test_iptables_chain_creation_and_flush_on_existing() -> None:
    recorded, runner = _ok_runner_factory()
    mgr = IptablesManager(runner=runner, require_linux=False)

    mgr.create_chain("SANDBOX_abc123")
    assert ["iptables", "-N", "SANDBOX_abc123"] in recorded
    assert "SANDBOX_abc123" in mgr.chains_created

    # Creating again should flush rather than fail
    mgr.create_chain("SANDBOX_abc123")
    assert ["iptables", "-F", "SANDBOX_abc123"] in recorded


def test_iptables_rule_addition_preserves_quoted_log_prefix() -> None:
    recorded, runner = _ok_runner_factory()
    mgr = IptablesManager(runner=runner, require_linux=False)
    mgr.create_chain("SANDBOX_log1")

    mgr.add_rule("SANDBOX_log1", '-j LOG --log-prefix "SANDBOX_BLOCK: " --log-level 4')
    assert recorded[-1] == [
        "iptables",
        "-A",
        "SANDBOX_log1",
        "-j",
        "LOG",
        "--log-prefix",
        "SANDBOX_BLOCK: ",
        "--log-level",
        "4",
    ]

    with pytest.raises(IptablesError, match="Unsafe character"):
        mgr.add_rule("SANDBOX_log1", "-j ACCEPT; rm -rf /")
    with pytest.raises(IptablesError, match="Invalid iptables chain name"):
        mgr.create_chain("bad;chain")


def test_iptables_chain_deletion_detaches_docker_user_and_cleanup_all() -> None:
    recorded, runner = _ok_runner_factory()
    mgr = IptablesManager(runner=runner, require_linux=False)

    mgr.create_chain("SANDBOX_del1")
    mgr.attach_to_parent_chain("DOCKER-USER", "SANDBOX_del1", ["-s", "172.17.0.5"])
    mgr.create_chain("SANDBOX_del2")

    mgr.delete_chain("SANDBOX_del1")
    detach_idx = recorded.index(
        ["iptables", "-D", "DOCKER-USER", "-s", "172.17.0.5", "-j", "SANDBOX_del1"]
    )
    delete_idx = recorded.index(["iptables", "-X", "SANDBOX_del1"])
    assert detach_idx < delete_idx
    assert "SANDBOX_del1" not in mgr.chains_created

    mgr.cleanup_all()
    assert mgr.chains_created == []
    assert ["iptables", "-X", "SANDBOX_del2"] in recorded


def test_sanitize_chain_name_unique_and_within_28_chars() -> None:
    c1 = sanitize_chain_name("pvs-sandbox-1700000000001")
    c2 = sanitize_chain_name("pvs-sandbox-1700000000002")
    assert c1 != c2
    assert len(c1) <= 28
    assert len(c2) <= 28
    assert c1.startswith("SANDBOX_")


async def test_network_filter_graceful_degradation_when_iptables_unavailable() -> None:
    unavailable_ipt = IptablesManager(
        runner=lambda _cmd: CommandResult(127, stderr="not found"),
        require_linux=False,
    )

    fallback_filter = NetworkFilter(
        {"filtering_enabled": True, "fallback_on_error": True, "allowlist": []},
        iptables=unavailable_ipt,
    )
    status = await fallback_filter.apply_rules("pvs-sandbox-1001")
    assert status.applied is False
    assert status.degraded_fallback is True

    strict_filter = NetworkFilter(
        {"filtering_enabled": True, "fallback_on_error": False, "allowlist": []},
        iptables=unavailable_ipt,
    )
    with pytest.raises(RuntimeError, match="iptables required"):
        await strict_filter.apply_rules("pvs-sandbox-1002")


async def test_network_filter_dns_caching_and_refresh() -> None:
    _, runner = _ok_runner_factory()
    ipt = IptablesManager(runner=runner, require_linux=False)
    calls = {"count": 0}

    def counting_dns(domain: str) -> list[str]:
        calls["count"] += 1
        return [f"104.18.6.{calls['count']}"]

    nf = NetworkFilter(
        {
            "filtering_enabled": True,
            "dns_cache_ttl_seconds": 300,
            "allowlist": [{"domain": "api.openai.com", "port": 443, "protocol": "tcp"}],
        },
        iptables=ipt,
        dns_resolver=counting_dns,
    )

    st1 = await nf.apply_rules("pvs-sandbox-c1")
    st2 = await nf.apply_rules("pvs-sandbox-c2")
    assert calls["count"] == 1
    assert st1.resolved_domains == {"api.openai.com": ["104.18.6.1"]}
    assert st2.resolved_domains == {"api.openai.com": ["104.18.6.1"]}

    st3 = await nf.refresh_rules("pvs-sandbox-c1")
    assert calls["count"] == 2
    assert st3.resolved_domains == {"api.openai.com": ["104.18.6.2"]}


def test_network_filter_policy_decisions_and_blocked_logging(tmp_path: Path) -> None:
    log_file = tmp_path / "sandbox_blocks.log"
    nf = NetworkFilter(
        {
            "filtering_enabled": True,
            "mode": "allowlist",
            "allowlist": [
                {"domain": "api.openai.com", "port": 443, "protocol": "tcp"},
                {"ip": "8.8.8.8", "port": 53, "protocol": "udp"},
            ],
            "log_blocked_connections": True,
            "log_path": str(log_file),
        }
    )

    assert nf.check_connection("api.openai.com", 443, "tcp").allowed is True
    assert nf.check_connection("127.0.0.1", 8080, "tcp").allowed is True
    assert nf.check_connection("localhost", 3000, "tcp").allowed is True
    assert nf.check_connection("8.8.8.8", 53, "udp").allowed is True
    assert nf.check_connection("1.1.1.1", 53, "tcp").allowed is True

    blocked = nf.check_connection("evil.example.com", 443, "tcp", container_id="pvs-sandbox-999")
    assert blocked.allowed is False
    assert blocked.reason == "blocked_not_in_allowlist"
    assert len(nf.blocked_events) == 1
    assert log_file.exists()
    assert "SANDBOX_BLOCK:" in log_file.read_text(encoding="utf-8")
    assert "evil.example.com:443/tcp" in log_file.read_text(encoding="utf-8")


def test_from_allowed_hosts_helper() -> None:
    nf = NetworkFilter.from_allowed_hosts(["api.openai.com", "10.0.0.5:8443"])
    assert nf.check_connection("api.openai.com", 443, "tcp").allowed is True
    assert nf.check_connection("10.0.0.5", 8443, "tcp").allowed is True
    assert nf.check_connection("127.0.0.1", 9000, "tcp").allowed is True
    assert nf.check_connection("example.com", 443, "tcp").allowed is False


def test_sandbox_cli_diagnostic_command(capsys: pytest.CaptureFixture[str]) -> None:
    rc = sandbox_cli_main(
        ["test-network-filter", "--config", str(REPO_ROOT / "config/sandbox.yaml")]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "[ALLOW] api.openai.com:443/tcp" in out
    assert "[ALLOW] 127.0.0.1:8080/tcp" in out
    assert "[BLOCK] example.com:443/tcp" in out


def test_sandbox_config_and_docs_zero_secrets() -> None:
    detector = HybridSecretDetector()
    for rel in ("config/sandbox.yaml", "docs/network-filtering.md"):
        findings = detector.detect((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"

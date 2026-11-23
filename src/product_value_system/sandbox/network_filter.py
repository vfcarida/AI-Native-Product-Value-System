"""
Fine-grained network egress filtering for Docker sandboxes (EVO-029).

Provides:
- Allowlist / denylist configuration parsing (``config/sandbox.yaml``)
- Validated IPv4 / CIDR / domain DNS resolution with TTL caching and backoff retries
- Deterministic iptables rule generation (localhost, DNS, conntrack, allowlist, LOG, REJECT)
- Graceful degradation on non-Linux hosts or environments without iptables privileges
- In-process egress policy evaluation and blocked-connection audit logging
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import shlex
import socket
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from product_value_system.sandbox.iptables_manager import (
    IptablesError,
    IptablesManager,
    sanitize_chain_name,
)

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace as _otel_trace

    _TRACER: Any | None = _otel_trace.get_tracer(__name__)
except Exception:
    _TRACER = None

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)
_WILDCARD_DOMAIN_RE = re.compile(
    r"^\*\.(?=.{1,251}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*\.?$"
)
_VALID_PROTOCOLS: frozenset[str] = frozenset({"tcp", "udp", "icmp", "any"})
_FORBIDDEN_IPTABLES_META: tuple[str, ...] = (";", "|", "&", "`", "$(", "\n", "\r")

DNSResolverFn = Callable[[str], list[str]]

DEFAULT_ALLOWLIST: list[dict[str, Any]] = [
    {"domain": "api.openai.com", "port": 443, "protocol": "tcp"},
    {"domain": "api.anthropic.com", "port": 443, "protocol": "tcp"},
    {"domain": "generativelanguage.googleapis.com", "port": 443, "protocol": "tcp"},
    {"domain": "pypi.org", "port": 443, "protocol": "tcp"},
    {"domain": "files.pythonhosted.org", "port": 443, "protocol": "tcp"},
    {"domain": "registry.npmjs.org", "port": 443, "protocol": "tcp"},
    {"ip": "8.8.8.8", "port": 53, "protocol": "udp"},
    {"ip": "1.1.1.1", "port": 53, "protocol": "udp"},
    {"ip": "127.0.0.1", "port": "any", "protocol": "any"},
]


def load_sandbox_network_config(path: Path | str) -> dict[str, Any]:
    """Load the ``sandbox.network`` configuration block from ``config/sandbox.yaml``."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return {}
    sandbox_block = raw.get("sandbox", raw)
    if isinstance(sandbox_block, dict) and "network" in sandbox_block:
        net = sandbox_block["network"]
        return dict(net) if isinstance(net, dict) else {}
    return dict(sandbox_block) if isinstance(sandbox_block, dict) else {}


def _system_dns_resolve(domain: str) -> list[str]:
    """Resolve ``domain`` to deduplicated sorted IPv4 addresses via ``socket.getaddrinfo``."""
    addr_info = socket.getaddrinfo(domain, None, family=socket.AF_INET)
    ips = sorted({str(info[4][0]) for info in addr_info if info[4]})
    return ips


@dataclass
class NetworkRule:
    """Represents a validated egress filtering rule (domain, wildcard, or IP/CIDR — PROD-003)."""

    destination: str
    port: int | str = 443
    protocol: str = "tcp"
    description: str = ""

    def __post_init__(self) -> None:
        dest = self.destination.strip()
        if not dest:
            raise ValueError("NetworkRule destination cannot be empty")
        if any(tok in dest for tok in _FORBIDDEN_IPTABLES_META) or " " in dest:
            raise ValueError(f"Unsafe characters in NetworkRule destination: {self.destination!r}")

        is_ip = False
        try:
            ipaddress.ip_network(dest, strict=False)
            is_ip = True
        except ValueError:
            is_ip = False

        if not is_ip:
            dest_norm = dest.lower().rstrip(".")
            if not (_DOMAIN_RE.match(dest_norm) or _WILDCARD_DOMAIN_RE.match(dest_norm)):
                raise ValueError(f"Invalid destination in NetworkRule: {self.destination!r}")
            self.destination = dest_norm
        else:
            self.destination = dest

        proto = str(self.protocol).strip().lower()
        if proto not in _VALID_PROTOCOLS:
            raise ValueError(
                f"Invalid protocol {self.protocol!r}; expected one of {sorted(_VALID_PROTOCOLS)}"
            )
        self.protocol = proto

        if isinstance(self.port, str):
            p_str = self.port.strip().lower()
            if p_str == "any":
                self.port = "any"
            else:
                p_int = int(p_str)
                if not (1 <= p_int <= 65535):
                    raise ValueError(f"Port must be 1..65535 or 'any', got {self.port!r}")
                self.port = p_int
        else:
            p_int = int(self.port)
            if not (1 <= p_int <= 65535):
                raise ValueError(f"Port must be 1..65535 or 'any', got {self.port!r}")
            self.port = p_int

    @property
    def is_wildcard(self) -> bool:
        return self.destination.startswith("*.")

    def to_allowlist_entry(self) -> AllowlistEntry | None:
        """Convert non-wildcard ``NetworkRule`` into an ``AllowlistEntry``."""
        if self.is_wildcard:
            return None
        try:
            ipaddress.ip_network(self.destination, strict=False)
            return AllowlistEntry(ip=self.destination, port=self.port, protocol=self.protocol)
        except ValueError:
            return AllowlistEntry(domain=self.destination, port=self.port, protocol=self.protocol)


def load_allowlist_from_config(config_path: Path | str) -> list[NetworkRule]:
    """Load validated ``NetworkRule`` list from a YAML allowlist file (PROD-003).

    Supports both ``config/network_allowlist.yaml`` (``destination`` entries) and
    ``config/sandbox.yaml`` (``sandbox.network.allowlist`` ``domain``/``ip`` entries).
    """
    cfg_path = Path(config_path)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return []

    if "sandbox" in raw and isinstance(raw["sandbox"], dict):
        entries_raw = raw["sandbox"].get("network", {}).get("allowlist", [])
    else:
        entries_raw = raw.get("allowlist", [])

    rules: list[NetworkRule] = []
    for entry in entries_raw:
        if not isinstance(entry, dict):
            continue
        dest = entry.get("destination") or entry.get("domain") or entry.get("ip")
        if not dest:
            continue
        rules.append(
            NetworkRule(
                destination=str(dest),
                port=entry.get("port", 443),
                protocol=str(entry.get("protocol", "tcp")),
                description=str(entry.get("description", "")),
            )
        )
    return rules


@dataclass
class AllowlistEntry:
    """Single network egress allowlist (or denylist) rule specification."""

    domain: str | None = None
    ip: str | None = None
    port: int | str = 443
    protocol: str = "tcp"

    def __post_init__(self) -> None:
        if not self.domain and not self.ip:
            raise ValueError("AllowlistEntry requires either 'domain' or 'ip'.")

        if self.domain is not None:
            cleaned_domain = self.domain.strip().lower().rstrip(".")
            if not _DOMAIN_RE.match(cleaned_domain):
                raise ValueError(f"Invalid domain name in allowlist entry: {self.domain!r}")
            self.domain = cleaned_domain

        if self.ip is not None:
            cleaned_ip = self.ip.strip()
            try:
                # Supports both single IP (8.8.8.8) and CIDR range (10.0.0.0/8)
                ipaddress.ip_network(cleaned_ip, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid IP/CIDR in allowlist entry: {self.ip!r}") from exc
            self.ip = cleaned_ip

        proto = str(self.protocol).strip().lower()
        if proto not in _VALID_PROTOCOLS:
            raise ValueError(
                f"Invalid protocol {self.protocol!r}; expected one of {sorted(_VALID_PROTOCOLS)}"
            )
        self.protocol = proto

        if isinstance(self.port, str):
            p_str = self.port.strip().lower()
            if p_str == "any":
                self.port = "any"
            else:
                p_int = int(p_str)
                if not (1 <= p_int <= 65535):
                    raise ValueError(f"Port must be 1..65535 or 'any', got {self.port!r}")
                self.port = p_int
        else:
            p_int = int(self.port)
            if not (1 <= p_int <= 65535):
                raise ValueError(f"Port must be 1..65535 or 'any', got {self.port!r}")
            self.port = p_int

    def resolve_ips(self, resolver: DNSResolverFn | None = None) -> list[str]:
        """Resolve domain to sorted IPv4 addresses, or return literal ``[self.ip]``."""
        if self.ip:
            return [self.ip]
        assert self.domain is not None
        resolve_fn = resolver or _system_dns_resolve
        try:
            raw_ips = resolve_fn(self.domain)
            validated: list[str] = []
            for candidate in raw_ips:
                try:
                    net = ipaddress.ip_network(candidate, strict=False)
                    if net.version == 4:
                        validated.append(candidate)
                except ValueError:
                    continue
            ips = sorted(set(validated))
            logger.info("Resolved %s to %d IPv4 address(es): %s", self.domain, len(ips), ips)
            return ips
        except (socket.gaierror, OSError) as exc:
            logger.error("Failed to resolve domain %s: %s", self.domain, exc)
            return []

    def to_iptables_rules(
        self,
        resolved_ips: list[str],
        *,
        action: str = "ACCEPT",
    ) -> list[str]:
        """Render iptables rule strings for each IP in ``resolved_ips``."""
        rules: list[str] = []
        for ip_addr in resolved_ips:
            parts = ["-d", ip_addr]
            if self.protocol != "any":
                parts.extend(["-p", self.protocol])
            if self.port != "any" and self.protocol in {"tcp", "udp"}:
                parts.extend(["--dport", str(self.port)])
            parts.extend(["-j", action])
            rules.append(" ".join(parts))
        return rules

    def matches_destination(
        self,
        host: str,
        port: int | str = 443,
        protocol: str = "tcp",
        resolved_ips: list[str] | None = None,
    ) -> bool:
        """Check whether a connection target matches this entry."""
        proto_l = protocol.strip().lower()
        if self.protocol != "any" and self.protocol != proto_l:
            return False
        if self.port != "any" and str(port).lower() != "any" and int(port) != int(self.port):
            return False

        target = host.strip().lower().rstrip(".")
        if self.domain and target == self.domain:
            return True

        try:
            target_ip = ipaddress.ip_address(target)
        except ValueError:
            target_ip = None

        if self.ip:
            entry_net = ipaddress.ip_network(self.ip, strict=False)
            if target_ip is not None and target_ip in entry_net:
                return True

        if resolved_ips and target_ip is not None:
            for ip_str in resolved_ips:
                if target_ip in ipaddress.ip_network(ip_str, strict=False):
                    return True

        return False


@dataclass
class EgressDecision:
    """Result of checking a destination against the configured egress filter policy."""

    allowed: bool
    host: str
    port: int | str
    protocol: str
    reason: str
    matched_entry: AllowlistEntry | None = None


@dataclass
class NetworkFilterStatus:
    """Outcome metadata returned by ``NetworkFilter.apply_rules()``."""

    container_id: str
    applied: bool
    degraded_fallback: bool
    chain_name: str | None = None
    rules_count: int = 0
    resolved_domains: dict[str, list[str]] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "container_id": self.container_id,
            "applied": self.applied,
            "degraded_fallback": self.degraded_fallback,
            "chain_name": self.chain_name,
            "rules_count": self.rules_count,
            "resolved_domains": dict(self.resolved_domains),
            "reason": self.reason,
        }


class NetworkFilter:
    """
    Allowlist/denylist network egress filter for Docker sandboxes.

    Enforces least-privilege outbound network access using dedicated per-container
    iptables chains on Linux, with automatic graceful degradation on non-Linux
    or non-privileged hosts when ``fallback_on_error`` is True.
    """

    def __init__(
        self,
        config: dict[str, Any] | Sequence[NetworkRule | AllowlistEntry] | None = None,
        *,
        iptables: IptablesManager | None = None,
        dns_resolver: DNSResolverFn | None = None,
        dns_cache_ttl_seconds: float | None = None,
        log_blocked_connections: bool | None = None,
        fallback_on_error: bool | None = None,
        wildcard_subdomains: dict[str, list[str]] | None = None,
    ) -> None:
        self.rules: list[NetworkRule] = []
        self._resolved_ips: set[str] = set()
        self._wildcard_subdomains: dict[str, list[str]] = {
            k.strip().lower(): list(v) for k, v in (wildcard_subdomains or {}).items()
        }

        if isinstance(config, Sequence) and not isinstance(config, (str, bytes)):
            raw: dict[str, Any] = {
                "filtering_enabled": True,
                "mode": "allowlist",
                "dns_cache_ttl_seconds": (
                    dns_cache_ttl_seconds if dns_cache_ttl_seconds is not None else 3600.0
                ),
                "log_blocked_connections": (
                    True if log_blocked_connections is None else log_blocked_connections
                ),
                "fallback_on_error": True if fallback_on_error is None else fallback_on_error,
            }
            entries_raw: list[Any] = []
            for item in config:
                if isinstance(item, NetworkRule):
                    self.rules.append(item)
                    converted = item.to_allowlist_entry()
                    if converted is not None:
                        entries_raw.append(converted)
                elif isinstance(item, AllowlistEntry):
                    entries_raw.append(item)
                    dest = item.ip or item.domain or ""
                    if dest:
                        self.rules.append(
                            NetworkRule(destination=dest, port=item.port, protocol=item.protocol)
                        )
            raw["allowlist"] = entries_raw
        else:
            raw = dict(config or {})
            if "sandbox" in raw and isinstance(raw["sandbox"], dict):
                raw = dict(raw["sandbox"].get("network", raw["sandbox"]))
            elif "network" in raw and isinstance(raw["network"], dict):
                raw = dict(raw["network"])

        self.config: dict[str, Any] = raw
        self.enabled: bool = bool(raw.get("filtering_enabled", False))
        self.mode: str = str(raw.get("mode", "allowlist")).strip().lower()
        if self.mode not in {"allowlist", "denylist"}:
            raise ValueError(f"Unsupported network filter mode: {self.mode!r}")

        self.log_blocked_connections: bool = (
            bool(log_blocked_connections)
            if log_blocked_connections is not None
            else bool(raw.get("log_blocked_connections", True))
        )
        self.log_path: str = str(raw.get("log_path", "/var/log/sandbox_network_blocks.log"))
        self.fallback_on_error: bool = (
            bool(fallback_on_error)
            if fallback_on_error is not None
            else bool(raw.get("fallback_on_error", True))
        )
        self.dns_cache_ttl_seconds: float = (
            float(dns_cache_ttl_seconds)
            if dns_cache_ttl_seconds is not None
            else float(raw.get("dns_cache_ttl_seconds", 300.0))
        )
        self.dns_resolve_retries: int = max(1, int(raw.get("dns_resolve_retries", 2)))

        entries_list = raw.get("allowlist", [])
        self.allowlist: list[AllowlistEntry] = [
            entry
            if isinstance(entry, AllowlistEntry)
            else AllowlistEntry(
                domain=entry.get("domain"),
                ip=entry.get("ip"),
                port=entry.get("port", 443),
                protocol=entry.get("protocol", "tcp"),
            )
            for entry in entries_list
        ]
        if not self.rules:
            for entry in self.allowlist:
                dest = entry.ip or entry.domain or ""
                if dest:
                    self.rules.append(
                        NetworkRule(destination=dest, port=entry.port, protocol=entry.protocol)
                    )

        self.iptables: IptablesManager = iptables or IptablesManager()
        self._dns_resolver: DNSResolverFn = dns_resolver or _system_dns_resolve
        self._dns_cache: dict[str, tuple[float, list[str]]] = {}
        self._container_chains: dict[str, str] = {}
        self.blocked_events: list[dict[str, Any]] = []

    @classmethod
    def from_yaml(
        cls,
        path: Path | str,
        *,
        iptables: IptablesManager | None = None,
        dns_resolver: DNSResolverFn | None = None,
    ) -> NetworkFilter:
        cfg = load_sandbox_network_config(path)
        return cls(cfg, iptables=iptables, dns_resolver=dns_resolver)

    @classmethod
    def from_allowed_hosts(
        cls,
        hosts: list[str],
        *,
        enabled: bool = True,
        fallback_on_error: bool = True,
        iptables: IptablesManager | None = None,
        dns_resolver: DNSResolverFn | None = None,
    ) -> NetworkFilter:
        """Build a ``NetworkFilter`` from host/IP strings (``SandboxConfig.allowed_hosts``)."""
        entries: list[dict[str, Any]] = []
        for raw_host in hosts:
            host = raw_host.strip()
            if not host:
                continue
            port: int | str = 443
            if ":" in host and not host.startswith("["):
                parts = host.rsplit(":", 1)
                if parts[1].isdigit():
                    host, port = parts[0], int(parts[1])
            try:
                ipaddress.ip_network(host, strict=False)
                entries.append({"ip": host, "port": port, "protocol": "tcp"})
            except ValueError:
                entries.append({"domain": host, "port": port, "protocol": "tcp"})
        # Always include localhost and standard DNS in host-list helper
        entries.extend(
            [
                {"ip": "8.8.8.8", "port": 53, "protocol": "udp"},
                {"ip": "1.1.1.1", "port": 53, "protocol": "udp"},
                {"ip": "127.0.0.1", "port": "any", "protocol": "any"},
            ]
        )
        return cls(
            {
                "filtering_enabled": enabled,
                "mode": "allowlist",
                "allowlist": entries,
                "fallback_on_error": fallback_on_error,
            },
            iptables=iptables,
            dns_resolver=dns_resolver,
        )

    def is_available(self) -> bool:
        """Return True if iptables enforcement is available on this host."""
        return self.iptables.is_available()

    @staticmethod
    def _is_ip_or_cidr(destination: str) -> bool:
        """Return True when ``destination`` is a valid IPv4/IPv6 address or CIDR range."""
        try:
            ipaddress.ip_network(destination.strip(), strict=False)
            return True
        except ValueError:
            return False

    def _resolve_domain(self, domain: str, *, force_refresh: bool = False) -> set[str]:
        """Resolve ``domain`` to IPv4 addresses with TTL caching (default 1h in PROD-003 mode)."""
        cleaned = domain.strip().lower().rstrip(".")
        now = time.monotonic()
        cached = self._dns_cache.get(cleaned)
        if (
            not force_refresh
            and cached is not None
            and (now - cached[0]) < self.dns_cache_ttl_seconds
        ):
            return set(cached[1])

        ips: list[str] = []
        try:
            raw_ips = self._dns_resolver(cleaned)
            validated: list[str] = []
            for candidate in raw_ips:
                try:
                    net = ipaddress.ip_network(candidate, strict=False)
                    if net.version == 4:
                        validated.append(candidate)
                except ValueError:
                    continue
            ips = sorted(set(validated))
            if ips:
                self._dns_cache[cleaned] = (time.monotonic(), list(ips))
        except (socket.gaierror, OSError) as exc:
            logger.warning("Failed to resolve %s: %s", cleaned, exc)
            if cached is not None:
                return set(cached[1])
        return set(ips)

    def reload_allowlist(self, config_path: Path | str) -> int:
        """Reload allowlist rules dynamically from YAML without restarting containers."""
        loaded = load_allowlist_from_config(config_path)
        self.rules = list(loaded)
        self.allowlist = [
            entry for rule in loaded if (entry := rule.to_allowlist_entry()) is not None
        ]
        self._dns_cache.clear()
        self._resolved_ips.clear()
        logger.info("Reloaded %d network allowlist rule(s) from %s", len(self.rules), config_path)
        return len(self.rules)

    def generate_iptables_rules(self, *, force_refresh_dns: bool = False) -> list[str]:
        """Generate complete in-container ``iptables`` commands enforcing default-deny egress."""
        commands: list[str] = [
            "iptables -F OUTPUT",
            "iptables -P OUTPUT DROP",
            "iptables -A OUTPUT -o lo -j ACCEPT",
            "iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT",
            "iptables -A OUTPUT -p udp --dport 53 -j ACCEPT",
            "iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT",
        ]

        # Collect concrete domains needing resolution and resolve concurrently for <50ms setup
        domains_to_resolve: list[str] = []
        for rule in self.rules:
            if self._is_ip_or_cidr(rule.destination):
                continue
            if rule.is_wildcard:
                subdomains = self._wildcard_subdomains.get(rule.destination, [])
                domains_to_resolve.extend(subdomains)
            else:
                domains_to_resolve.append(rule.destination)

        unique_domains = list(dict.fromkeys(domains_to_resolve))
        resolved_map: dict[str, list[str]] = {}
        if unique_domains:
            uncached = [
                d
                for d in unique_domains
                if force_refresh_dns or d not in self._dns_cache
            ]
            if len(uncached) > 1:
                with ThreadPoolExecutor(max_workers=min(8, len(uncached))) as pool:
                    futures = {
                        d: pool.submit(self._resolve_domain, d, force_refresh=force_refresh_dns)
                        for d in uncached
                    }
                    for domain, fut in futures.items():
                        resolved_map[domain] = sorted(fut.result())
            for domain in unique_domains:
                if domain not in resolved_map:
                    resolved_map[domain] = sorted(
                        self._resolve_domain(domain, force_refresh=force_refresh_dns)
                    )

        def _append_rule(ip_dest: str, rule: NetworkRule) -> None:
            parts = ["iptables", "-A", "OUTPUT"]
            if rule.protocol != "any":
                parts.extend(["-p", rule.protocol])
            parts.extend(["-d", ip_dest])
            if rule.port != "any" and rule.protocol in {"tcp", "udp"}:
                parts.extend(["--dport", str(rule.port)])
            parts.extend(["-j", "ACCEPT"])
            commands.append(" ".join(parts))

        for rule in self.rules:
            if self._is_ip_or_cidr(rule.destination):
                _append_rule(rule.destination, rule)
            elif rule.is_wildcard:
                subdomains = self._wildcard_subdomains.get(rule.destination, [])
                if not subdomains:
                    logger.debug(
                        "Wildcard allowlist rule %s requires pre-resolved subdomains for iptables",
                        rule.destination,
                    )
                for sub in subdomains:
                    for ip_addr in resolved_map.get(sub, []):
                        _append_rule(ip_addr, rule)
                        self._resolved_ips.add(ip_addr)
            else:
                for ip_addr in resolved_map.get(rule.destination, []):
                    _append_rule(ip_addr, rule)
                    self._resolved_ips.add(ip_addr)

        if self.log_blocked_connections:
            commands.append(
                'iptables -A OUTPUT -j LOG --log-prefix "SANDBOX-DENIED: " --log-level 4'
            )

        return commands

    @staticmethod
    def _validate_iptables_command(cmd: str) -> list[str]:
        """Ensure an iptables command string is strictly safe (no shell metacharacters)."""
        stripped = cmd.strip()
        if not stripped.startswith("iptables "):
            raise ValueError(f"Refusing non-iptables command: {cmd!r}")
        if any(meta in stripped for meta in _FORBIDDEN_IPTABLES_META):
            raise ValueError(f"Forbidden shell metacharacter in iptables command: {cmd!r}")
        return shlex.split(stripped)

    @staticmethod
    def apply_rules_in_container(
        container: Any,
        commands: Sequence[str],
        *,
        fallback_on_error: bool = False,
    ) -> float:
        """Execute validated ``iptables`` commands inside a running Docker container.

        Returns:
            Elapsed rule-application latency in milliseconds.
        """
        parsed_cmds = [NetworkFilter._validate_iptables_command(cmd) for cmd in commands]
        started = time.perf_counter()
        span_cm = (
            _TRACER.start_as_current_span("sandbox.network_filter.apply")
            if _TRACER is not None
            else nullcontext()
        )
        with span_cm as span:
            applied = True
            for raw_cmd, argv in zip(commands, parsed_cmds, strict=True):
                try:
                    result = container.exec_run(argv, privileged=True, user="0")
                except TypeError:
                    # Compatibility with minimal mock containers accepting (cmd, privileged=...)
                    result = container.exec_run(raw_cmd, privileged=True)
                exit_code, output = result
                if exit_code != 0:
                    decoded = (
                        output.decode("utf-8", errors="replace")
                        if isinstance(output, (bytes, bytearray))
                        else str(output)
                    )
                    msg = f"iptables command failed: {raw_cmd}\nOutput: {decoded}"
                    if fallback_on_error:
                        logger.warning("%s; continuing without egress filter (fallback)", msg)
                        applied = False
                        break
                    raise RuntimeError(msg)
                logger.debug("Applied container rule: %s", raw_cmd)

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if span is not None:
                span.set_attribute("sandbox.network_filter.rules_count", len(parsed_cmds))
                span.set_attribute("sandbox.network_filter.latency_ms", round(elapsed_ms, 3))
                span.set_attribute("sandbox.network_filter.applied", applied)
            return elapsed_ms

    def _resolve_entry_ips_cached(
        self,
        entry: AllowlistEntry,
        *,
        force_refresh: bool = False,
    ) -> list[str]:
        if entry.ip:
            return [entry.ip]
        assert entry.domain is not None

        now = time.monotonic()
        cached = self._dns_cache.get(entry.domain)
        if (
            not force_refresh
            and cached is not None
            and (now - cached[0]) < self.dns_cache_ttl_seconds
        ):
            return list(cached[1])

        last_ips: list[str] = []
        for attempt in range(self.dns_resolve_retries):
            last_ips = entry.resolve_ips(resolver=self._dns_resolver)
            if last_ips:
                self._dns_cache[entry.domain] = (time.monotonic(), list(last_ips))
                return last_ips
            if attempt < self.dns_resolve_retries - 1:
                time.sleep(min(0.05 * (2**attempt), 0.5))

        # Preserve stale cache if live resolution failed temporarily
        if cached is not None:
            return list(cached[1])
        return []

    async def _resolve_all_entries(
        self,
        *,
        force_refresh: bool = False,
    ) -> list[tuple[AllowlistEntry, list[str]]]:
        """Resolve all allowlist entries concurrently to minimize container start latency."""
        loop = asyncio.get_running_loop()

        async def _resolve_one(entry: AllowlistEntry) -> tuple[AllowlistEntry, list[str]]:
            ips = await loop.run_in_executor(
                None,
                lambda: self._resolve_entry_ips_cached(entry, force_refresh=force_refresh),
            )
            return entry, ips

        return list(await asyncio.gather(*(_resolve_one(e) for e in self.allowlist)))

    def generate_rules(
        self,
        resolved_entries: list[tuple[AllowlistEntry, list[str]]],
    ) -> list[str]:
        """Build the complete ordered list of iptables rules for a sandbox chain."""
        rules: list[str] = [
            # 1. Always permit loopback (harness communication)
            "-o lo -j ACCEPT",
            # 2. Always permit DNS resolution (UDP + TCP port 53)
            "-p udp --dport 53 -j ACCEPT",
            "-p tcp --dport 53 -j ACCEPT",
            # 3. Always permit established / related return traffic
            "-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        ]

        if self.mode == "allowlist":
            for entry, ips in resolved_entries:
                rules.extend(entry.to_iptables_rules(ips, action="ACCEPT"))

            if self.log_blocked_connections:
                rules.append('-j LOG --log-prefix "SANDBOX_BLOCK: " --log-level 4')

            rules.append("-j REJECT --reject-with icmp-port-unreachable")
        else:
            # Denylist mode: log & reject listed entries, permit everything else
            for entry, ips in resolved_entries:
                if self.log_blocked_connections:
                    rules.extend(
                        entry.to_iptables_rules(
                            ips,
                            action='LOG --log-prefix "SANDBOX_BLOCK: " --log-level 4',
                        )
                    )
                rules.extend(
                    entry.to_iptables_rules(
                        ips,
                        action="REJECT --reject-with icmp-port-unreachable",
                    )
                )
            rules.append("-j ACCEPT")

        return rules

    async def apply_rules(
        self,
        container_id: str,
        *,
        container_ip: str | None = None,
        force_refresh_dns: bool = False,
    ) -> NetworkFilterStatus:
        """Apply iptables egress rules for ``container_id`` (with optional graceful fallback)."""
        if not self.enabled:
            logger.info("Network filtering disabled for container %s", container_id[:12])
            return NetworkFilterStatus(
                container_id=container_id,
                applied=False,
                degraded_fallback=False,
                reason="filtering_disabled",
            )

        if not self.is_available():
            msg = "iptables not available on this host"
            logger.warning("%s; skipping egress filtering for %s", msg, container_id[:12])
            if self.fallback_on_error:
                return NetworkFilterStatus(
                    container_id=container_id,
                    applied=False,
                    degraded_fallback=True,
                    reason=msg,
                )
            raise RuntimeError("iptables required for network egress filtering but not available")

        chain_name = sanitize_chain_name(container_id)
        resolved_entries = await self._resolve_all_entries(force_refresh=force_refresh_dns)
        resolved_domains = {
            entry.domain: list(ips) for entry, ips in resolved_entries if entry.domain is not None
        }
        rules = self.generate_rules(resolved_entries)

        try:
            self.iptables.create_chain(chain_name)
            for rule in rules:
                self.iptables.add_rule(chain_name, rule)

            await self._apply_chain_to_container(
                container_id,
                chain_name,
                container_ip=container_ip,
            )
            self._container_chains[container_id] = chain_name
            logger.info(
                "Applied %d egress rules in chain %s for container %s",
                len(rules),
                chain_name,
                container_id[:12],
            )
            return NetworkFilterStatus(
                container_id=container_id,
                applied=True,
                degraded_fallback=False,
                chain_name=chain_name,
                rules_count=len(rules),
                resolved_domains=resolved_domains,
                reason="applied",
            )
        except (IptablesError, RuntimeError) as exc:
            logger.error("Failed applying egress rules for %s: %s", container_id[:12], exc)
            if self.fallback_on_error:
                try:
                    self.iptables.delete_chain(chain_name)
                except Exception as cleanup_exc:
                    logger.debug(
                        "Best-effort chain cleanup failed for %s: %s",
                        chain_name,
                        cleanup_exc,
                    )
                return NetworkFilterStatus(
                    container_id=container_id,
                    applied=False,
                    degraded_fallback=True,
                    reason=str(exc),
                )
            raise

    async def refresh_rules(
        self,
        container_id: str,
        *,
        container_ip: str | None = None,
    ) -> NetworkFilterStatus:
        """Re-resolve allowlist domains and refresh iptables rules for ``container_id``."""
        return await self.apply_rules(
            container_id,
            container_ip=container_ip,
            force_refresh_dns=True,
        )

    async def remove_rules(self, container_id: str) -> None:
        """Remove iptables chain and hooks associated with ``container_id``."""
        if not self.enabled:
            return
        chain_name = self._container_chains.pop(container_id, None)
        if chain_name is None:
            if not self.is_available():
                return
            chain_name = sanitize_chain_name(container_id)
            if chain_name not in self.iptables.chains_created:
                return

        logger.info("Removing network filtering chain %s for %s", chain_name, container_id[:12])
        try:
            self.iptables.delete_chain(chain_name)
        except IptablesError as exc:
            logger.warning("Failed removing chain %s: %s", chain_name, exc)

    async def _apply_chain_to_container(
        self,
        container_id: str,
        chain_name: str,
        *,
        container_ip: str | None = None,
    ) -> None:
        """Attach ``chain_name`` to ``DOCKER-USER`` for ``container_ip`` when provided."""
        if container_ip:
            ipaddress.ip_address(container_ip)  # Validate IP before passing to iptables
            self.iptables.attach_to_parent_chain("DOCKER-USER", chain_name, ["-s", container_ip])
            logger.info(
                "Attached chain %s to DOCKER-USER for container %s (%s)",
                chain_name,
                container_id[:12],
                container_ip,
            )

    def check_connection(
        self,
        host: str,
        port: int | str = 443,
        protocol: str = "tcp",
        *,
        container_id: str = "local",
    ) -> EgressDecision:
        """Evaluate whether an outbound connection is permitted by this filter policy."""
        if not self.enabled:
            return EgressDecision(True, host, port, protocol, "filtering_disabled")

        target = host.strip().lower().rstrip(".")
        proto_l = protocol.strip().lower()

        # Invariant 2: localhost always allowed
        if target in {"localhost", "127.0.0.1", "::1"}:
            return EgressDecision(True, host, port, proto_l, "localhost_always_allowed")

        # Invariant 3: DNS resolution always allowed
        if str(port) == "53" and proto_l in {"udp", "tcp"}:
            return EgressDecision(True, host, port, proto_l, "dns_always_allowed")

        matched: AllowlistEntry | None = None
        for entry in self.allowlist:
            resolved = self._dns_cache.get(entry.domain, (0.0, []))[1] if entry.domain else None
            if entry.matches_destination(
                target,
                port=port,
                protocol=proto_l,
                resolved_ips=resolved,
            ):
                matched = entry
                break

        if self.mode == "allowlist":
            if matched is not None:
                return EgressDecision(True, host, port, proto_l, "matched_allowlist", matched)
            decision = EgressDecision(False, host, port, proto_l, "blocked_not_in_allowlist")
            self._record_blocked(decision, container_id=container_id)
            return decision

        # Denylist mode
        if matched is not None:
            decision = EgressDecision(False, host, port, proto_l, "blocked_by_denylist", matched)
            self._record_blocked(decision, container_id=container_id)
            return decision
        return EgressDecision(True, host, port, proto_l, "allowed_not_in_denylist")

    def _record_blocked(self, decision: EgressDecision, *, container_id: str) -> None:
        if not self.log_blocked_connections:
            return
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "container_id": container_id,
            "host": decision.host,
            "port": decision.port,
            "protocol": decision.protocol,
            "reason": decision.reason,
        }
        self.blocked_events.append(event)
        logger.warning(
            "SANDBOX_BLOCK: container=%s dest=%s:%s/%s reason=%s",
            container_id[:12],
            decision.host,
            decision.port,
            decision.protocol,
            decision.reason,
        )
        try:
            log_file = Path(self.log_path)
            if log_file.parent.exists():
                with log_file.open("a", encoding="utf-8") as fh:
                    fh.write(
                        f"{event['timestamp']} SANDBOX_BLOCK: container={container_id} "
                        f"dest={decision.host}:{decision.port}/{decision.protocol} "
                        f"reason={decision.reason}\n"
                    )
        except OSError:
            # Non-writable system log path (e.g., /var/log on Windows/non-root); keep in memory
            pass

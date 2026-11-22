"""CLI diagnostic entrypoint: ``python -m product_value_system.sandbox test-network-filter``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from product_value_system.sandbox.network_filter import NetworkFilter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m product_value_system.sandbox",
        description="Sandbox network egress filter diagnostics (EVO-029)",
    )
    parser.add_argument(
        "command",
        choices=["test-network-filter"],
        help="Diagnostic action to run",
    )
    parser.add_argument(
        "--config",
        default="config/sandbox.yaml",
        help="Path to sandbox YAML configuration",
    )
    args = parser.parse_args(argv)

    cfg_path = Path(args.config)
    net_filter = (
        NetworkFilter.from_yaml(cfg_path)
        if cfg_path.exists()
        else NetworkFilter({"filtering_enabled": True, "allowlist": []})
    )

    print(f"Network filtering enabled: {net_filter.enabled} (mode={net_filter.mode})")
    print(f"iptables available on host: {net_filter.is_available()}")
    print(f"Allowlist entries: {len(net_filter.allowlist)}")

    checks = [
        ("api.openai.com", 443, "tcp"),
        ("pypi.org", 443, "tcp"),
        ("127.0.0.1", 8080, "tcp"),
        ("8.8.8.8", 53, "udp"),
        ("example.com", 443, "tcp"),
    ]
    for host, port, proto in checks:
        dec = net_filter.check_connection(host, port=port, protocol=proto)
        status = "ALLOW" if dec.allowed else "BLOCK"
        print(f"  [{status}] {host}:{port}/{proto} ({dec.reason})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

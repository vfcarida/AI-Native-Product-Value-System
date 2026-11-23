#!/usr/bin/env python3
"""
Parse Trivy JSON vulnerability reports and generate actionable triage summaries (PROD-005).

Usage:
    python scripts/analyze-vulnerabilities.py trivy-report.json
    python scripts/analyze-vulnerabilities.py trivy-report.json --fail-on HIGH --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

SEVERITY_ORDER: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")


def analyze_trivy_report(report_path: str | Path) -> dict[str, Any]:
    """Parse a Trivy JSON report and return normalized severity, package, and fixability stats."""
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))

    by_severity: dict[str, int] = defaultdict(int)
    by_package: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_vulns = 0
    fixable = 0
    unfixable = 0

    for result in data.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            total_vulns += 1
            severity = str(vuln.get("Severity") or "UNKNOWN").upper()
            by_severity[severity] += 1

            pkg_name = str(vuln.get("PkgName") or "unknown")
            fixed_version = vuln.get("FixedVersion") or None
            by_package[pkg_name].append(
                {
                    "cve": str(vuln.get("VulnerabilityID") or "UNKNOWN"),
                    "severity": severity,
                    "installed_version": str(vuln.get("InstalledVersion") or ""),
                    "fixed_version": str(fixed_version) if fixed_version else None,
                    "title": str(vuln.get("Title") or ""),
                }
            )
            if fixed_version:
                fixable += 1
            else:
                unfixable += 1

    return {
        "total_vulns": total_vulns,
        "by_severity": {sev: by_severity.get(sev, 0) for sev in SEVERITY_ORDER},
        "by_package": dict(by_package),
        "fixable": fixable,
        "unfixable": unfixable,
    }


def format_report(stats: dict[str, Any]) -> str:
    """Render a deterministic human-readable triage report from ``analyze_trivy_report()``."""
    lines: list[str] = [
        "=" * 80,
        "VULNERABILITY ANALYSIS REPORT",
        "=" * 80,
        "",
        f"Total Vulnerabilities: {stats['total_vulns']}",
        "",
        "By Severity:",
    ]

    for severity in SEVERITY_ORDER:
        count = int(stats["by_severity"].get(severity, 0))
        if count > 0:
            lines.append(f"  {severity:12s}: {count:3d}")

    total = int(stats["total_vulns"])
    fixable = int(stats["fixable"])
    unfixable = int(stats["unfixable"])
    fix_pct = (fixable / total * 100.0) if total > 0 else 0.0
    unfix_pct = (unfixable / total * 100.0) if total > 0 else 0.0
    lines.extend(
        [
            "",
            f"Fixable:   {fixable:3d} ({fix_pct:.1f}%)",
            f"Unfixable: {unfixable:3d} ({unfix_pct:.1f}%)",
            "",
            "Top 10 Vulnerable Packages:",
        ]
    )

    top_packages = sorted(
        stats["by_package"].items(),
        key=lambda item: len(item[1]),
        reverse=True,
    )[:10]
    if not top_packages:
        lines.append("  (none)")
    else:
        for pkg, vulns in top_packages:
            c_count = sum(1 for v in vulns if v["severity"] == "CRITICAL")
            h_count = sum(1 for v in vulns if v["severity"] == "HIGH")
            lines.append(f"  {pkg:30s}: {len(vulns):2d} vulns (C:{c_count}, H:{h_count})")

    lines.extend(["", "=" * 80, "ACTION ITEMS", "=" * 80, ""])
    critical_fixable = [
        (pkg, v)
        for pkg, vulns in stats["by_package"].items()
        for v in vulns
        if v["severity"] == "CRITICAL" and v.get("fixed_version")
    ]
    if critical_fixable:
        lines.append("CRITICAL VULNERABILITIES WITH FIXES AVAILABLE:")
        for pkg, vuln in critical_fixable[:10]:
            lines.append(f"  - {pkg}: {vuln['cve']} -> Upgrade to {vuln['fixed_version']}")
    else:
        lines.append("No critical vulnerabilities with available fixes.")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze-vulnerabilities",
        description="Analyze Trivy JSON vulnerability scan output (PROD-005).",
    )
    parser.add_argument("report", type=Path, help="Path to Trivy JSON scan report")
    parser.add_argument(
        "--fail-on",
        choices=("CRITICAL", "HIGH", "NONE"),
        default="CRITICAL",
        help="Minimum severity triggering non-zero exit code (default: CRITICAL)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON summary instead of text table",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stats = analyze_trivy_report(args.report)

    if args.json_output:
        print(json.dumps(stats, indent=2))
    else:
        print(format_report(stats))

    by_sev = stats["by_severity"]
    if args.fail_on == "CRITICAL" and by_sev.get("CRITICAL", 0) > 0:
        return 1
    if args.fail_on == "HIGH" and (by_sev.get("CRITICAL", 0) + by_sev.get("HIGH", 0)) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

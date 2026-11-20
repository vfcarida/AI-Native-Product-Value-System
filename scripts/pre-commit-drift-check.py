#!/usr/bin/env python3
"""
Pre-commit hook for automated code-to-specification drift detection (EVO-017).

Blocks commits when CRITICAL drift is detected, when MAJOR findings exceed
the configured threshold (default: 3), or when drift exceeds 5% of checked specs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from product_value_system.drift.comparator import DriftSeverity
from product_value_system.drift.detector import scan_drift
from product_value_system.drift.reporter import DriftReporter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-commit specification drift gate")
    parser.add_argument("--code", nargs="+", default=["src/"], help="Code path(s) to scan")
    parser.add_argument("--spec", nargs="+", default=["docs/"], help="Spec path(s) to scan")
    parser.add_argument(
        "--severity",
        default="critical,major",
        help="Comma-separated severities to report (default: critical,major)",
    )
    parser.add_argument("--baseline", type=Path, default=None, help="Optional baseline JSON")
    parser.add_argument("--max-major", type=int, default=3, help="Max allowed MAJOR findings")
    parser.add_argument(
        "--max-drift-ratio",
        type=float,
        default=0.05,
        help="Max allowed diverged ratio (default: 0.05 = 5%%)",
    )
    args = parser.parse_args(argv)

    severities = {DriftSeverity[s.strip().upper()] for s in args.severity.split(",") if s.strip()}
    scan = scan_drift(
        code_paths=args.code,
        spec_paths=args.spec,
        baseline_path=args.baseline,
    )
    filtered = [f for f in scan.findings if f.severity in severities]

    reporter = DriftReporter()
    should_block = reporter.should_block_commit(
        filtered,
        max_major=args.max_major,
        max_drift_ratio=args.max_drift_ratio,
        total_checked=scan.total_checked or None,
    )

    if should_block:
        print(f"Drift detection blocked commit ({len(filtered)} critical/major findings):")
        for finding in filtered:
            print(f"  [{finding.severity.value.upper()}] {finding.description}")
            print(f"    Code: {finding.code_location}")
            print(f"    Spec: {finding.spec_location}")
            print(f"    Fix:  {finding.suggestion}")
        print("\nRun `pvs drift report --output drift_report.md` for full details.")
        return 1

    print(f"Drift detection passed ({len(filtered)} blocking findings).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

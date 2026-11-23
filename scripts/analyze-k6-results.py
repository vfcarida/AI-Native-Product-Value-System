#!/usr/bin/env python3
"""Analyze k6 JSON summary results and detect performance regressions (PROD-009)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PerformanceMetrics:
    """Normalized performance metrics parsed from a k6 ``--summary-export`` JSON file."""

    p50_latency: float
    p95_latency: float
    p99_latency: float
    error_rate: float
    throughput: float
    total_requests: int
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "p50_latency_ms": round(self.p50_latency, 3),
            "p95_latency_ms": round(self.p95_latency, 3),
            "p99_latency_ms": round(self.p99_latency, 3),
            "error_rate": round(self.error_rate, 6),
            "throughput_rps": round(self.throughput, 3),
            "total_requests": self.total_requests,
            "tokens_used": self.tokens_used,
        }


DEFAULT_THRESHOLDS: dict[str, float] = {
    "p99_latency_max_increase": 10.0,  # +10% max latency regression
    "error_rate_max_increase": 50.0,  # +50% relative error-rate regression
    "throughput_max_decrease": 10.0,  # -10% max throughput regression
}


def parse_k6_summary(summary_path: Path | str) -> PerformanceMetrics:
    """Parse a k6 JSON summary export file into ``PerformanceMetrics``."""
    data = json.loads(Path(summary_path).read_text(encoding="utf-8"))
    metrics = data.get("metrics") or {}

    dur_values = (metrics.get("http_req_duration") or {}).get("values") or {}
    req_values = (metrics.get("http_reqs") or {}).get("values") or {}

    p50 = float(dur_values.get("p(50)", dur_values.get("med", 0.0)))
    p95 = float(dur_values.get("p(95)", 0.0))
    p99 = float(dur_values.get("p(99)", 0.0))

    if "errors" in metrics and "rate" in (metrics["errors"].get("values") or {}):
        err_rate = float(metrics["errors"]["values"]["rate"])
    elif "http_req_failed" in metrics:
        fail_vals = metrics["http_req_failed"].get("values") or {}
        err_rate = float(fail_vals.get("rate", fail_vals.get("value", 0.0)))
    else:
        err_rate = 0.0

    throughput = float(req_values.get("rate", 0.0))
    total_requests = int(req_values.get("count", 0))
    tokens_used = int(((metrics.get("tokens_used") or {}).get("values") or {}).get("count", 0))

    return PerformanceMetrics(
        p50_latency=p50,
        p95_latency=p95,
        p99_latency=p99,
        error_rate=err_rate,
        throughput=throughput,
        total_requests=total_requests,
        tokens_used=tokens_used,
    )


def compare_baselines(
    current: PerformanceMetrics,
    baseline: PerformanceMetrics,
) -> dict[str, float]:
    """Compute percentage changes from ``baseline`` to ``current`` safely."""

    def _pct_change(curr: float, base: float, *, floor: float = 1e-6) -> float:
        denom = max(base, floor)
        return ((curr - base) / denom) * 100.0

    return {
        "p50_latency_change": _pct_change(current.p50_latency, baseline.p50_latency),
        "p95_latency_change": _pct_change(current.p95_latency, baseline.p95_latency),
        "p99_latency_change": _pct_change(current.p99_latency, baseline.p99_latency),
        "error_rate_change": _pct_change(current.error_rate, baseline.error_rate, floor=0.001),
        "throughput_change": _pct_change(current.throughput, baseline.throughput),
    }


def detect_regression(
    changes: dict[str, float],
    thresholds: dict[str, float] | None = None,
    *,
    current: PerformanceMetrics | None = None,
    slo_p99_max_ms: float | None = None,
) -> list[str]:
    """Return human-readable regression violations exceeding configured thresholds."""
    limits = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        limits.update(thresholds)

    violations: list[str] = []

    if changes["p99_latency_change"] > limits["p99_latency_max_increase"]:
        violations.append(
            f"p99 latency increased by {changes['p99_latency_change']:.1f}% "
            f"(threshold: {limits['p99_latency_max_increase']:.1f}%)"
        )

    if changes["error_rate_change"] > limits["error_rate_max_increase"]:
        violations.append(
            f"Error rate increased by {changes['error_rate_change']:.1f}% "
            f"(threshold: {limits['error_rate_max_increase']:.1f}%)"
        )

    if changes["throughput_change"] < -limits["throughput_max_decrease"]:
        violations.append(
            f"Throughput decreased by {abs(changes['throughput_change']):.1f}% "
            f"(threshold: {limits['throughput_max_decrease']:.1f}%)"
        )

    if (
        current is not None
        and slo_p99_max_ms is not None
        and current.p99_latency > slo_p99_max_ms
    ):
        violations.append(
            f"p99 latency {current.p99_latency:.1f}ms exceeds SLO ceiling {slo_p99_max_ms:.1f}ms"
        )

    return violations


def format_markdown_report(
    current: PerformanceMetrics,
    baseline: PerformanceMetrics,
    changes: dict[str, float],
    violations: list[str],
) -> str:
    """Format a Markdown PR comment summarizing baseline comparison and SLO compliance."""
    status = "REGRESSION DETECTED" if violations else "PASS — Within SLO & Baseline Thresholds"
    rows = [
        (
            "p50 Latency",
            f"{baseline.p50_latency:.1f} ms",
            f"{current.p50_latency:.1f} ms",
            changes["p50_latency_change"],
        ),
        (
            "p95 Latency",
            f"{baseline.p95_latency:.1f} ms",
            f"{current.p95_latency:.1f} ms",
            changes["p95_latency_change"],
        ),
        (
            "p99 Latency",
            f"{baseline.p99_latency:.1f} ms",
            f"{current.p99_latency:.1f} ms",
            changes["p99_latency_change"],
        ),
        (
            "Error Rate",
            f"{baseline.error_rate:.2%}",
            f"{current.error_rate:.2%}",
            changes["error_rate_change"],
        ),
        (
            "Throughput",
            f"{baseline.throughput:.2f} req/s",
            f"{current.throughput:.2f} req/s",
            changes["throughput_change"],
        ),
    ]
    lines = [
        "## Load Test Regression Analysis",
        "",
        f"**Status**: {status}",
        "",
        "| Metric | Baseline | Current | Change |",
        "|---|---:|---:|---:|",
        *(f"| {label} | {b} | {c} | {delta:+.1f}% |" for label, b, c, delta in rows),
        "",
    ]
    if violations:
        lines.append("### Violations")
        for item in violations:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze k6 summary against performance baseline")
    parser.add_argument("current_summary", type=Path, help="Path to current k6 JSON summary")
    parser.add_argument("baseline_summary", type=Path, help="Path to baseline k6 JSON summary")
    parser.add_argument("--max-p99-increase-pct", type=float, default=10.0)
    parser.add_argument("--max-error-increase-pct", type=float, default=50.0)
    parser.add_argument("--max-throughput-decrease-pct", type=float, default=10.0)
    parser.add_argument("--slo-p99-max-ms", type=float, default=None)
    parser.add_argument("--markdown-out", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    current = parse_k6_summary(args.current_summary)
    baseline = parse_k6_summary(args.baseline_summary)
    changes = compare_baselines(current, baseline)
    thresholds = {
        "p99_latency_max_increase": args.max_p99_increase_pct,
        "error_rate_max_increase": args.max_error_increase_pct,
        "throughput_max_decrease": args.max_throughput_decrease_pct,
    }
    violations = detect_regression(
        changes,
        thresholds,
        current=current,
        slo_p99_max_ms=args.slo_p99_max_ms,
    )

    report_md = format_markdown_report(current, baseline, changes, violations)
    print(report_md)

    if args.markdown_out is not None:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(report_md, encoding="utf-8")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "baseline": baseline.to_dict(),
            "current": current.to_dict(),
            "changes_pct": {k: round(v, 2) for k, v in changes.items()},
            "violations": violations,
            "passed": len(violations) == 0,
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

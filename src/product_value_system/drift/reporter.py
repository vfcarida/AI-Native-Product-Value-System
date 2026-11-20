"""
Drift report generation (JSON, Markdown, weekly digest), commit gating,
and brownfield baseline filtering (EVO-017).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.drift.comparator import DriftFinding, DriftSeverity


class DriftReporter:
    """Generate structured drift reports, weekly summaries, and pre-commit gate decisions."""

    def summarize(
        self,
        findings: list[DriftFinding],
        *,
        total_checked: int | None = None,
    ) -> dict[str, Any]:
        """Compute severity breakdown and optional drift percentage."""
        critical = sum(1 for f in findings if f.severity == DriftSeverity.CRITICAL)
        major = sum(1 for f in findings if f.severity == DriftSeverity.MAJOR)
        moderate = sum(1 for f in findings if f.severity == DriftSeverity.MODERATE)
        minor = sum(1 for f in findings if f.severity == DriftSeverity.MINOR)
        diverged_locations = len({f.code_location for f in findings})
        drift_ratio = (
            round(diverged_locations / max(1, total_checked), 4)
            if total_checked is not None and total_checked > 0
            else 0.0
        )
        return {
            "total_findings": len(findings),
            "diverged_items": diverged_locations,
            "total_checked": total_checked,
            "drift_ratio": drift_ratio,
            "critical": critical,
            "major": major,
            "moderate": moderate,
            "minor": minor,
        }

    def generate_report(
        self,
        findings: list[DriftFinding],
        output_path: str | Path,
        *,
        total_checked: int | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        """Write comprehensive JSON drift report and return its summary dict."""
        summary = self.summarize(findings, total_checked=total_checked)
        report: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "since": since,
            "summary": summary,
            "findings": [f.to_dict() for f in findings],
        }
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return summary

    def generate_markdown_report(
        self,
        findings: list[DriftFinding],
        output_path: str | Path,
        *,
        total_checked: int | None = None,
        since: str | None = None,
    ) -> str:
        """Write human-readable Markdown drift report grouped by severity."""
        summary = self.summarize(findings, total_checked=total_checked)
        lines: list[str] = ["# Drift Detection Report\n"]
        if since:
            lines.append(f"**Window**: Since {since}  ")
        lines.append(
            f"**Summary**: {summary['diverged_items']} item(s) diverged "
            f"({summary['total_findings']} total findings)\n"
        )
        lines.append("## Summary\n")
        lines.append(f"- **Total Findings**: {summary['total_findings']}")
        lines.append(f"- **Critical**: {summary['critical']}")
        lines.append(f"- **Major**: {summary['major']}")
        lines.append(f"- **Moderate**: {summary['moderate']}")
        lines.append(f"- **Minor**: {summary['minor']}")
        if total_checked is not None:
            lines.append(
                f"- **Drift Rate**: {summary['drift_ratio']:.1%} "
                f"({summary['diverged_items']}/{total_checked})"
            )
        lines.append("")

        for severity in (
            DriftSeverity.CRITICAL,
            DriftSeverity.MAJOR,
            DriftSeverity.MODERATE,
            DriftSeverity.MINOR,
        ):
            sev_findings = [f for f in findings if f.severity == severity]
            if not sev_findings:
                continue
            lines.append(f"## {severity.value.upper()} Findings\n")
            for finding in sev_findings:
                lines.append(f"### {finding.description}\n")
                lines.append(f"- **Category**: `{finding.category}`")
                lines.append(f"- **Code**: `{finding.code_location}`")
                lines.append(f"- **Spec**: `{finding.spec_location}`")
                lines.append(f"- **Suggestion**: {finding.suggestion}\n")
                if finding.details:
                    lines.append("```json")
                    lines.append(json.dumps(finding.details, indent=2))
                    lines.append("```\n")

        markdown = "\n".join(lines)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(markdown, encoding="utf-8")
        return markdown

    def should_block_commit(
        self,
        findings: list[DriftFinding],
        *,
        max_major: int = 3,
        max_drift_ratio: float = 0.05,
        total_checked: int | None = None,
    ) -> bool:
        """
        Return True if detected drift exceeds configured commit-blocking thresholds:
        - Any ``CRITICAL`` finding
        - More than ``max_major`` (default: 3) ``MAJOR`` findings
        - Drift ratio exceeding ``max_drift_ratio`` (default: 5%) when ``total_checked`` is known
        """
        if any(f.severity == DriftSeverity.CRITICAL for f in findings):
            return True
        major_count = sum(1 for f in findings if f.severity == DriftSeverity.MAJOR)
        if major_count > max_major:
            return True
        if total_checked is not None and total_checked > 0:
            diverged = len({f.code_location for f in findings})
            if (diverged / total_checked) > max_drift_ratio:
                return True
        return False

    def filter_baseline(
        self,
        findings: list[DriftFinding],
        baseline_path: str | Path,
    ) -> list[DriftFinding]:
        """Exclude findings already recorded in a brownfield baseline JSON snapshot."""
        path = Path(baseline_path)
        if not path.exists():
            return findings
        baseline = json.loads(path.read_text(encoding="utf-8"))
        known_fps: set[str] = set()
        for item in baseline.get("findings", []):
            if isinstance(item, dict) and item.get("fingerprint"):
                known_fps.add(str(item["fingerprint"]))
        return [f for f in findings if f.fingerprint() not in known_fps]

"""
OpenTelemetry trace scanner and behavioral SLA drift comparison (EVO-017).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from product_value_system.drift.comparator import DriftFinding, DriftSeverity
from product_value_system.drift.spec_scanner import BehavioralSpec


@dataclass
class TraceSummary:
    """Aggregated runtime performance and error profile for a traced operation."""

    operation: str
    sample_count: int
    mean_duration_ms: float
    p50_duration_ms: float
    p95_duration_ms: float
    error_rate: float


def _percentile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * q)))
    return float(ordered[idx])


class TraceScanner:
    """Load OpenTelemetry span exports and compare observed behavior with specifications."""

    def load_spans(self, trace_path: str | Path) -> list[dict[str, Any]]:
        """Load spans from JSON (``{"spans": [...]}`` or ``[...]``) or JSONL export."""
        path = Path(trace_path)
        if not path.exists():
            return []

        if path.is_dir():
            all_spans: list[dict[str, Any]] = []
            for file_path in sorted(path.rglob("*.json*")):
                all_spans.extend(self.load_spans(file_path))
            return all_spans

        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return []

        if path.suffix.lower() == ".jsonl":
            spans: list[dict[str, Any]] = []
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        spans.append(obj)
            return spans

        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("spans"), list):
            return [s for s in parsed["spans"] if isinstance(s, dict)]
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
        return []

    def aggregate_spans(self, spans: list[dict[str, Any]]) -> dict[str, TraceSummary]:
        """Aggregate raw spans by operation name into latency and error summaries."""
        buckets: dict[str, list[tuple[float, bool]]] = {}

        for span in spans:
            operation = str(span.get("operation") or span.get("name") or "").strip()
            if not operation:
                continue
            duration_ms = float(span.get("duration_ms", 0.0))
            status = str(span.get("status", "ok")).lower()
            is_error = bool(span.get("error")) or status in {"error", "failed", "failure"}
            buckets.setdefault(operation, []).append((duration_ms, is_error))

        summaries: dict[str, TraceSummary] = {}
        for operation, records in buckets.items():
            durations = sorted(r[0] for r in records)
            errors = sum(1 for r in records if r[1])
            count = len(records)
            summaries[operation] = TraceSummary(
                operation=operation,
                sample_count=count,
                mean_duration_ms=round(sum(durations) / count, 3),
                p50_duration_ms=round(_percentile(durations, 0.50), 3),
                p95_duration_ms=round(_percentile(durations, 0.95), 3),
                error_rate=round(errors / count, 6),
            )
        return summaries

    def compare_traces_with_spec(
        self,
        spans_or_summaries: list[dict[str, Any]] | dict[str, TraceSummary],
        behavioral_specs: list[BehavioralSpec],
    ) -> list[DriftFinding]:
        """Compare observed OpenTelemetry trace summaries against behavioral SLA specs."""
        summaries = (
            spans_or_summaries
            if isinstance(spans_or_summaries, dict)
            else self.aggregate_spans(spans_or_summaries)
        )
        findings: list[DriftFinding] = []

        for spec in behavioral_specs:
            matched_ops = self._match_operations(spec.operation, summaries)
            for summary in matched_ops:
                if (
                    spec.max_latency_ms is not None
                    and summary.mean_duration_ms > spec.max_latency_ms
                ):
                    ratio = summary.mean_duration_ms / max(spec.max_latency_ms, 1e-9)
                    severity = DriftSeverity.CRITICAL if ratio >= 2.0 else DriftSeverity.MAJOR
                    spec_range = (
                        f"{spec.min_latency_ms / 1000.0:.1f}-{spec.max_latency_ms / 1000.0:.1f}s"
                        if spec.min_latency_ms is not None
                        else f"<= {spec.max_latency_ms / 1000.0:.1f}s"
                    )
                    mean_s = summary.mean_duration_ms / 1000.0
                    findings.append(
                        DriftFinding(
                            severity=severity,
                            category="behavior",
                            description=(
                                f"Actual performance for '{summary.operation}' exceeds specified "
                                f"SLA ({mean_s:.2f}s avg vs {spec_range})"
                            ),
                            code_location=f"trace::{summary.operation}",
                            spec_location=f"{spec.source_file}:L{spec.line_number}",
                            suggestion=(
                                f"Optimize '{summary.operation}' or update SLA at "
                                f"{spec.source_file}:L{spec.line_number}"
                            ),
                            details={
                                "operation": summary.operation,
                                "spec_max_latency_ms": spec.max_latency_ms,
                                "observed_mean_ms": summary.mean_duration_ms,
                                "observed_p50_ms": summary.p50_duration_ms,
                                "observed_p95_ms": summary.p95_duration_ms,
                                "samples": summary.sample_count,
                                "clause": spec.raw_clause,
                            },
                        )
                    )

                if (
                    spec.max_error_rate is not None
                    and summary.error_rate > spec.max_error_rate
                ):
                    findings.append(
                        DriftFinding(
                            severity=DriftSeverity.CRITICAL,
                            category="behavior",
                            description=(
                                f"Observed error rate for '{summary.operation}' "
                                f"({summary.error_rate:.2%}) exceeds specified "
                                f"{spec.max_error_rate:.2%} SLA"
                            ),
                            code_location=f"trace::{summary.operation}",
                            spec_location=f"{spec.source_file}:L{spec.line_number}",
                            suggestion=(
                                f"Investigate elevated errors in '{summary.operation}' "
                                "or adjust specification error budget"
                            ),
                            details={
                                "operation": summary.operation,
                                "spec_max_error_rate": spec.max_error_rate,
                                "observed_error_rate": summary.error_rate,
                                "samples": summary.sample_count,
                            },
                        )
                    )
        return findings

    def _match_operations(
        self,
        spec_operation: str,
        summaries: dict[str, TraceSummary],
    ) -> list[TraceSummary]:
        if spec_operation == "*":
            return list(summaries.values())
        if spec_operation in summaries:
            return [summaries[spec_operation]]
        lowered = spec_operation.lower()
        return [
            summary
            for op, summary in summaries.items()
            if lowered in op.lower() or op.lower() in lowered
        ]

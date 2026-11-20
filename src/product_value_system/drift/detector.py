"""
High-level drift detection orchestrator combining Code, Spec, and Trace scanners (EVO-017).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from product_value_system.drift.code_scanner import APIEndpoint, CodeScanner, FunctionSignature
from product_value_system.drift.comparator import DriftComparator, DriftFinding, DriftSeverity
from product_value_system.drift.reporter import DriftReporter
from product_value_system.drift.spec_scanner import (
    BehavioralSpec,
    SpecEndpoint,
    SpecFunction,
    SpecScanner,
)
from product_value_system.drift.trace_scanner import TraceScanner, TraceSummary
from product_value_system.observability.tracing import traced


@dataclass
class DriftScanResult:
    """Complete result from scanning code, specs, and optional OpenTelemetry traces."""

    findings: list[DriftFinding] = field(default_factory=list)
    code_functions_count: int = 0
    code_endpoints_count: int = 0
    spec_functions_count: int = 0
    spec_endpoints_count: int = 0
    behavioral_specs_count: int = 0

    @property
    def total_checked(self) -> int:
        return self.spec_functions_count + self.spec_endpoints_count + self.behavioral_specs_count


def compare_traces_with_spec(
    traces: list[dict[str, Any]] | dict[str, TraceSummary] | str | Path,
    specs: list[BehavioralSpec] | str | Path,
) -> list[DriftFinding]:
    """Compare OpenTelemetry traces against behavioral SLA specifications."""
    trace_scanner = TraceScanner()
    spec_scanner = SpecScanner()

    resolved_traces: list[dict[str, Any]] | dict[str, TraceSummary]
    if isinstance(traces, str | Path):
        resolved_traces = trace_scanner.load_spans(traces)
    else:
        resolved_traces = traces

    if isinstance(specs, str | Path):
        _, _, resolved_specs = spec_scanner.scan_directory(specs)
    else:
        resolved_specs = specs

    return trace_scanner.compare_traces_with_spec(resolved_traces, resolved_specs)


@traced("drift.detect")
def scan_drift(
    code_paths: list[str | Path],
    spec_paths: list[str | Path],
    *,
    trace_paths: list[str | Path] | None = None,
    include_behavior: bool = False,
    llm_callable: Callable[..., str] | None = None,
    baseline_path: str | Path | None = None,
    allowlist: set[str] | None = None,
    alert_threshold: int | None = None,
    alert_callback: Callable[[str], Any] | None = None,
) -> DriftScanResult:
    """
    Scan codebases, specifications, and optional OpenTelemetry traces for drift.

    Works gracefully with brownfield codebases even when specifications are partial
    or absent (zero false positives when no spec exists).
    """
    code_scanner = CodeScanner()
    spec_scanner = SpecScanner()
    comparator = DriftComparator(llm_callable=llm_callable, allowlist=allowlist)

    code_functions: list[FunctionSignature] = []
    code_endpoints: list[APIEndpoint] = []
    for cp in code_paths:
        code_functions.extend(code_scanner.scan_directory(cp))
        code_endpoints.extend(code_scanner.scan_fastapi_directory(cp))

    spec_functions: list[SpecFunction] = []
    spec_endpoints: list[SpecEndpoint] = []
    behavioral_specs: list[BehavioralSpec] = []
    for sp in spec_paths:
        s_funcs, s_eps, s_behav = spec_scanner.scan_directory(sp)
        spec_functions.extend(s_funcs)
        spec_endpoints.extend(s_eps)
        behavioral_specs.extend(s_behav)

    findings: list[DriftFinding] = []

    # Index code functions by name (support multiple modules cleanly)
    code_by_name: dict[str, list[FunctionSignature]] = {}
    for sig in code_functions:
        code_by_name.setdefault(sig.name, []).append(sig)
        if sig.class_name:
            code_by_name.setdefault(sig.qualified_name, []).append(sig)

    for spec_func in spec_functions:
        candidates = code_by_name.get(spec_func.name, [])
        if not candidates:
            findings.append(
                DriftFinding(
                    severity=DriftSeverity.CRITICAL,
                    category="signature",
                    description=(
                        f"Function `{spec_func.name}()` is documented in specification "
                        "but missing from scanned code"
                    ),
                    code_location=f"missing::{spec_func.name}",
                    spec_location=f"{spec_func.source_file}:L{spec_func.line_number}",
                    suggestion=(
                        f"Remove `{spec_func.name}()` from `{spec_func.source_file}` "
                        f"line {spec_func.line_number} or restore implementation"
                    ),
                    details={"function": spec_func.name},
                )
            )
            continue
        # Compare against the closest matching signature (minimum findings avoids false positives
        # when multiple unrelated modules define the same generic helper name)
        best_findings = min(
            (comparator.compare_functions(candidate, spec_func) for candidate in candidates),
            key=len,
        )
        findings.extend(best_findings)

    # Index code endpoints by (METHOD, path)
    code_ep_map: dict[tuple[str, str], APIEndpoint] = {
        (ep.method.upper(), ep.path): ep for ep in code_endpoints
    }
    for spec_ep in spec_endpoints:
        key = (spec_ep.method.upper(), spec_ep.path)
        code_ep = code_ep_map.get(key)
        if code_ep is None:
            findings.append(
                DriftFinding(
                    severity=DriftSeverity.MAJOR,
                    category="endpoint",
                    description=(
                        f"Endpoint {spec_ep.method} {spec_ep.path} documented in OpenAPI "
                        "specification was removed from code"
                    ),
                    code_location=f"missing::{spec_ep.method} {spec_ep.path}",
                    spec_location=spec_ep.source_file,
                    suggestion=(
                        f"Remove {spec_ep.method} {spec_ep.path} from `{spec_ep.source_file}` "
                        "or restore route handler"
                    ),
                    details={"method": spec_ep.method, "path": spec_ep.path},
                )
            )
            continue
        findings.extend(comparator.compare_endpoints(code_ep, spec_ep))

    # Optional behavioral trace comparison (OpenTelemetry spans vs SLA specs)
    if include_behavior or trace_paths:
        trace_scanner = TraceScanner()
        all_spans: list[dict[str, Any]] = []
        for tp in trace_paths or []:
            all_spans.extend(trace_scanner.load_spans(tp))
        if all_spans and behavioral_specs:
            findings.extend(trace_scanner.compare_traces_with_spec(all_spans, behavioral_specs))

    # Brownfield baseline filtering (ignore pre-existing accepted drift)
    if baseline_path is not None:
        findings = DriftReporter().filter_baseline(findings, baseline_path)

    # Alert when drift count exceeds configured threshold
    if alert_threshold is not None and len(findings) >= alert_threshold and alert_callback:
        alert_callback(
            f"Drift alert: detected {len(findings)} divergence findings "
            f"(threshold={alert_threshold})"
        )

    return DriftScanResult(
        findings=findings,
        code_functions_count=len(code_functions),
        code_endpoints_count=len(code_endpoints),
        spec_functions_count=len(spec_functions),
        spec_endpoints_count=len(spec_endpoints),
        behavioral_specs_count=len(behavioral_specs),
    )


def detect_drift(
    code_paths: list[str | Path],
    spec_paths: list[str | Path],
    *,
    trace_paths: list[str | Path] | None = None,
    include_behavior: bool = False,
    llm_callable: Callable[..., str] | None = None,
    baseline_path: str | Path | None = None,
    allowlist: set[str] | None = None,
    alert_threshold: int | None = None,
    alert_callback: Callable[[str], Any] | None = None,
) -> list[DriftFinding]:
    """Convenience function returning the list of detected ``DriftFinding`` objects."""
    result: DriftScanResult = scan_drift(
        code_paths=code_paths,
        spec_paths=spec_paths,
        trace_paths=trace_paths,
        include_behavior=include_behavior,
        llm_callable=llm_callable,
        baseline_path=baseline_path,
        allowlist=allowlist,
        alert_threshold=alert_threshold,
        alert_callback=alert_callback,
    )
    return result.findings

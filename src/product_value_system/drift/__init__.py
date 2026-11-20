"""
Automated Drift Detection between code, specifications, and OpenTelemetry traces (EVO-017).
"""

from product_value_system.drift.code_scanner import APIEndpoint, CodeScanner, FunctionSignature
from product_value_system.drift.comparator import DriftComparator, DriftFinding, DriftSeverity
from product_value_system.drift.detector import (
    DriftScanResult,
    compare_traces_with_spec,
    detect_drift,
    scan_drift,
)
from product_value_system.drift.reporter import DriftReporter
from product_value_system.drift.spec_scanner import (
    BehavioralSpec,
    SpecEndpoint,
    SpecFunction,
    SpecScanner,
)
from product_value_system.drift.trace_scanner import TraceScanner, TraceSummary

__all__ = [
    "APIEndpoint",
    "BehavioralSpec",
    "CodeScanner",
    "DriftComparator",
    "DriftFinding",
    "DriftReporter",
    "DriftScanResult",
    "DriftSeverity",
    "FunctionSignature",
    "SpecEndpoint",
    "SpecFunction",
    "SpecScanner",
    "TraceScanner",
    "TraceSummary",
    "compare_traces_with_spec",
    "detect_drift",
    "scan_drift",
]

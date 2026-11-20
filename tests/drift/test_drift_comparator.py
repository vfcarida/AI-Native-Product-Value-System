"""
Unit tests for EVO-017 DriftComparator, TraceScanner, and DriftReporter.
"""

import json
import os
from pathlib import Path

import pytest

from product_value_system.drift.code_scanner import APIEndpoint, FunctionSignature
from product_value_system.drift.comparator import DriftComparator, DriftFinding, DriftSeverity
from product_value_system.drift.reporter import DriftReporter
from product_value_system.drift.spec_scanner import BehavioralSpec, SpecEndpoint, SpecFunction
from product_value_system.drift.trace_scanner import TraceScanner


def _base_code_sig(
    params: list[dict[str, object]] | None = None,
    return_type: str = "bool",
    docstring: str = "Execute payment transfer.",
) -> FunctionSignature:
    return FunctionSignature(
        name="transfer",
        module="payment.py",
        parameters=params
        if params is not None
        else [
            {"name": "amount", "type": "float", "default": None},
            {"name": "destination", "type": "str", "default": None},
        ],
        return_type=return_type,
        docstring=docstring,
    )


def _base_spec_func(
    params: list[dict[str, object]] | None = None,
    returns: str = "bool",
    description: str = "Execute payment transfer.",
) -> SpecFunction:
    return SpecFunction(
        name="transfer",
        description=description,
        parameters=params
        if params is not None
        else [
            {"name": "amount", "type": "float", "default": None},
            {"name": "destination", "type": "str", "default": None},
        ],
        returns=returns,
        source_file="docs/api.md",
        line_number=42,
    )


@pytest.mark.unit
def test_exact_match_produces_zero_false_positives() -> None:
    """Matching code and spec produce zero drift findings."""
    findings = DriftComparator().compare_functions(_base_code_sig(), _base_spec_func())
    assert findings == []


@pytest.mark.unit
def test_equivalent_typing_aliases_produce_zero_false_positives() -> None:
    """Dict/List/Optional aliases normalize identically to dict/list/|None."""
    code = _base_code_sig(
        params=[{"name": "meta", "type": "dict[str, list[int]] | None", "default": "None"}],
        return_type="dict[str, str]",
    )
    spec = _base_spec_func(
        params=[{"name": "meta", "type": "Optional[Dict[str, List[int]]]", "default": "None"}],
        returns="Dict[str, str]",
    )
    assert DriftComparator().compare_functions(code, spec) == []


@pytest.mark.unit
def test_detect_added_optional_parameter_is_moderate() -> None:
    """Adding an optional parameter (with default) is classified MODERATE."""
    code = _base_code_sig(
        params=[
            {"name": "amount", "type": "float", "default": None},
            {"name": "destination", "type": "str", "default": None},
            {"name": "biometric", "type": "bool", "default": "False"},
        ]
    )
    findings = DriftComparator().compare_functions(code, _base_spec_func())
    assert len(findings) == 1
    assert findings[0].severity == DriftSeverity.MODERATE
    assert "biometric" in findings[0].description
    assert "line 42" in findings[0].suggestion


@pytest.mark.unit
def test_detect_added_required_parameter_is_major() -> None:
    """Adding a required parameter (no default) is classified MAJOR."""
    code = _base_code_sig(
        params=[
            {"name": "amount", "type": "float", "default": None},
            {"name": "destination", "type": "str", "default": None},
            {"name": "idempotency_key", "type": "str", "default": None},
        ]
    )
    findings = DriftComparator().compare_functions(code, _base_spec_func())
    assert len(findings) == 1
    assert findings[0].severity == DriftSeverity.MAJOR
    assert "idempotency_key" in findings[0].description


@pytest.mark.unit
def test_detect_removed_parameter_is_critical() -> None:
    """Removing a documented parameter from code is classified CRITICAL."""
    code = _base_code_sig(params=[{"name": "amount", "type": "float", "default": None}])
    findings = DriftComparator().compare_functions(code, _base_spec_func())
    assert len(findings) == 1
    assert findings[0].severity == DriftSeverity.CRITICAL
    assert "destination" in findings[0].description


@pytest.mark.unit
def test_detect_parameter_type_and_return_type_mismatch() -> None:
    """Parameter type change and return type change are both classified MAJOR."""
    code = _base_code_sig(
        params=[
            {"name": "amount", "type": "Decimal", "default": None},
            {"name": "destination", "type": "str", "default": None},
        ],
        return_type="TransferResult",
    )
    findings = DriftComparator().compare_functions(code, _base_spec_func())
    assert len(findings) == 2
    assert all(f.severity == DriftSeverity.MAJOR for f in findings)
    assert any("amount" in f.description for f in findings)
    assert any("Return type" in f.description for f in findings)


@pytest.mark.unit
def test_detect_default_value_change_is_minor() -> None:
    """Changing an explicitly documented default value is classified MINOR."""
    code = _base_code_sig(params=[{"name": "retries", "type": "int", "default": "5"}])
    spec = _base_spec_func(params=[{"name": "retries", "type": "int", "default": "3"}])
    findings = DriftComparator().compare_functions(code, spec)
    assert len(findings) == 1
    assert findings[0].severity == DriftSeverity.MINOR


@pytest.mark.unit
def test_semantic_docstring_drift_uses_cached_llm_callable() -> None:
    """Semantic comparison detects docstring divergence and caches identical pairs."""
    calls: list[str] = []

    def _mock_llm(prompt: str, temperature: float = 0.0) -> str:  # noqa: ARG001
        calls.append(prompt)
        return json.dumps(
            {
                "equivalent": False,
                "difference": "Code uses progressive fee tiers (2%, 1.5%, 1%) vs flat 2% in spec",
            }
        )

    comparator = DriftComparator(llm_callable=_mock_llm)
    code = _base_code_sig(docstring="Calculate progressive fees: 2%, 1.5%, 1%.")
    spec = _base_spec_func(description="Calculate flat 2% fee on all transfers.")

    findings_1 = comparator.compare_functions(code, spec)
    findings_2 = comparator.compare_functions(code, spec)

    assert len(findings_1) == 1
    assert findings_1[0].category == "documentation"
    assert findings_1[0].severity == DriftSeverity.MODERATE
    assert len(findings_2) == 1
    assert len(calls) == 1  # Cached on second call


@pytest.mark.unit
def test_semantic_docstring_equivalent_and_llm_error_resilience() -> None:
    """Equivalent LLM judgment or LLM exception produces zero false positives."""
    ok_comp = DriftComparator(
        llm_callable=lambda _p, **_k: json.dumps({"equivalent": True, "difference": ""})
    )
    assert (
        ok_comp.compare_functions(
            _base_code_sig(docstring="Run payment transfer."),
            _base_spec_func(description="Execute payment transfer."),
        )
        == []
    )

    def _boom(_p: str, **_k: object) -> str:
        raise RuntimeError("LLM unavailable")

    err_comp = DriftComparator(llm_callable=_boom)
    assert (
        err_comp.compare_functions(
            _base_code_sig(docstring="Run payment transfer."),
            _base_spec_func(description="Execute payment transfer."),
        )
        == []
    )


@pytest.mark.unit
def test_compare_endpoints_detects_added_and_removed_request_params() -> None:
    """Compare FastAPI route request parameters against OpenAPI properties."""
    code_ep = APIEndpoint(
        path="/transfers",
        method="POST",
        handler="create_transfer",
        request_schema={"amount": "float", "biometric_token": "str"},
        response_schema={"type": "dict"},
    )
    spec_ep = SpecEndpoint(
        path="/transfers",
        method="POST",
        description="Create transfer",
        request_body={"properties": {"amount": {"type": "number"}, "destination": {"type": "str"}}},
        source_file="openapi.yaml",
    )
    findings = DriftComparator().compare_endpoints(code_ep, spec_ep)
    assert len(findings) == 2
    by_sev = {f.severity: f for f in findings}
    assert "biometric_token" in by_sev[DriftSeverity.MAJOR].description
    assert "destination" in by_sev[DriftSeverity.CRITICAL].description


@pytest.mark.unit
def test_allowlist_suppresses_intentional_divergence() -> None:
    """Allowlisted functions produce zero drift findings."""
    code = _base_code_sig(params=[{"name": "amount", "type": "float", "default": None}])
    comparator = DriftComparator(allowlist={"transfer"})
    assert comparator.compare_functions(code, _base_spec_func()) == []


@pytest.mark.unit
def test_check_doc_freshness_flags_stale_documentation(tmp_path: Path) -> None:
    """Flag stale documentation when code was modified >7 days after spec."""
    code_file = tmp_path / "service.py"
    spec_file = tmp_path / "service.md"
    code_file.write_text("def foo() -> None: pass\n", encoding="utf-8")
    spec_file.write_text("# Spec\n", encoding="utf-8")

    now = code_file.stat().st_mtime
    os.utime(spec_file, (now - 86400 * 14, now - 86400 * 14))

    findings = DriftComparator().check_doc_freshness(code_file, spec_file)
    assert len(findings) == 1
    assert findings[0].category == "stale_doc"
    assert findings[0].severity == DriftSeverity.MINOR


@pytest.mark.unit
def test_trace_scanner_detects_latency_and_error_rate_sla_drift() -> None:
    """OpenTelemetry spans exceeding latency or error-rate SLAs produce behavioral findings."""
    spans = [
        {"operation": "initiate_transfer", "duration_ms": 8234, "status": "ok"},
        {"operation": "initiate_transfer", "duration_ms": 7891, "status": "ok"},
        {"operation": "initiate_transfer", "duration_ms": 8567, "status": "error"},
    ]
    specs = [
        BehavioralSpec(
            operation="initiate_transfer",
            min_latency_ms=2000.0,
            max_latency_ms=5000.0,
            max_error_rate=0.05,
            source_file="docs/performance.md",
            line_number=4,
            raw_clause="Transfer operations should complete in 2-5 seconds",
        )
    ]
    findings = TraceScanner().compare_traces_with_spec(spans, specs)
    assert len(findings) == 2
    assert all(f.category == "behavior" for f in findings)
    assert any(f.severity == DriftSeverity.MAJOR and "8.23s" in f.description for f in findings)
    assert any(
        f.severity == DriftSeverity.CRITICAL and "error rate" in f.description for f in findings
    )


@pytest.mark.unit
def test_trace_scanner_within_sla_produces_zero_findings() -> None:
    """Spans satisfying latency and error-rate SLAs produce zero behavioral drift."""
    spans = [
        {"operation": "initiate_transfer", "duration_ms": 2100, "status": "ok"},
        {"operation": "initiate_transfer", "duration_ms": 3400, "status": "ok"},
    ]
    specs = [
        BehavioralSpec(
            operation="initiate_transfer",
            min_latency_ms=2000.0,
            max_latency_ms=5000.0,
            max_error_rate=0.01,
            source_file="docs/performance.md",
            line_number=4,
        )
    ]
    assert TraceScanner().compare_traces_with_spec(spans, specs) == []


@pytest.mark.unit
def test_reporter_json_markdown_baseline_and_commit_blocking(tmp_path: Path) -> None:
    """DriftReporter writes JSON/Markdown, filters baselines, and enforces blocking thresholds."""
    reporter = DriftReporter()
    major_findings = [
        DriftFinding(
            severity=DriftSeverity.MAJOR,
            category="signature",
            description=f"Added required param p{i}",
            code_location=f"mod.py::fn_{i}",
            spec_location=f"docs/api.md:L{i + 1}",
            suggestion=f"Add p{i} to spec",
        )
        for i in range(4)
    ]

    # >3 MAJOR blocks commit; <=3 MAJOR passes if drift ratio <=5%
    assert reporter.should_block_commit(major_findings) is True
    assert reporter.should_block_commit(major_findings[:2], total_checked=100) is False
    assert reporter.should_block_commit(major_findings[:1], total_checked=10) is True  # 10% > 5%

    critical = [
        DriftFinding(
            severity=DriftSeverity.CRITICAL,
            category="signature",
            description="Removed param",
            code_location="mod.py::fn_0",
            spec_location="docs/api.md:L1",
            suggestion="Restore param",
        )
    ]
    assert reporter.should_block_commit(critical) is True

    # JSON & Markdown generation + baseline filtering
    json_path = tmp_path / "baseline.json"
    md_path = tmp_path / "weekly_drift.md"
    summary = reporter.generate_report(
        major_findings, json_path, total_checked=50, since="7 days ago"
    )
    md_text = reporter.generate_markdown_report(
        major_findings, md_path, total_checked=50, since="7 days ago"
    )
    assert summary["major"] == 4
    assert summary["diverged_items"] == 4
    assert md_path.exists()
    assert "7 days ago" in md_text
    assert "MAJOR Findings" in md_text

    # Baseline suppresses pre-existing findings, leaving only newly introduced ones
    remaining = reporter.filter_baseline(major_findings + critical, json_path)
    assert len(remaining) == 1
    assert remaining[0].severity == DriftSeverity.CRITICAL

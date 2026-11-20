"""
Integration tests for EVO-019 StabilityChecker, ValidationGate, ABTest auto-validation,
ValidatedExperimentRunner, and CLI `pvs experiment validate-baseline`.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from product_value_system.cli.main import main as cli_main
from product_value_system.experiment.ab_test import ABTest
from product_value_system.experiment.models import (
    ExperimentDesign,
    ExperimentHypothesis,
    ExperimentStatus,
)
from product_value_system.validation import (
    BaselineCollector,
    BaselineData,
    PowerAnalyzer,
    StabilityChecker,
    StatisticalValidator,
    ValidatedExperimentRunner,
    ValidationGate,
)


class _StubTracker:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items

    def query(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self._items)


def _good_baseline(n: int = 200, seed: int = 42) -> BaselineData:
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    return BaselineData(
        metric_name="latency_ms",
        values=list(rng.normal(loc=100.0, scale=10.0, size=n)),
        timestamps=[t0 + timedelta(minutes=i) for i in range(n)],
    )


@pytest.mark.integration
def test_stability_checker_detects_temporal_drift_between_halves() -> None:
    """Mann-Whitney U test detects distribution shift between first and second halves."""
    rng = np.random.default_rng(42)
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    first_half = list(rng.normal(loc=100.0, scale=5.0, size=50))
    second_half = list(rng.normal(loc=140.0, scale=5.0, size=50))
    drifting = BaselineData(
        metric_name="latency_ms",
        values=first_half + second_half,
        timestamps=[t0 + timedelta(minutes=i) for i in range(100)],
    )

    result = StabilityChecker().check_stability(drifting)

    assert result.stable is False
    assert result.drift_detected is True
    assert result.drift_p_value < 0.05
    assert any("drift" in w.lower() for w in result.warnings)


@pytest.mark.integration
def test_stability_checker_detects_incidents_and_deployments() -> None:
    """StabilityChecker flags incidents and deploys occurring during baseline collection."""
    baseline = _good_baseline(n=100)
    checker = StabilityChecker(
        incident_tracker=_StubTracker([{"id": "INC-401"}]),
        deploy_tracker=_StubTracker([{"id": "DEP-88"}, {"id": "DEP-89"}]),
    )

    result = checker.check_stability(baseline)

    assert result.stable is False
    assert result.incidents_during_baseline == ["INC-401"]
    assert result.deploys_during_baseline == ["DEP-88", "DEP-89"]
    assert len(result.warnings) >= 2


@pytest.mark.integration
def test_stability_checker_passes_stationary_incident_free_baseline() -> None:
    """Stationary baseline with zero incidents/deploys reports stable=True."""
    result = StabilityChecker().check_stability(_good_baseline(n=100))
    assert result.stable is True
    assert result.drift_detected is False
    assert result.warnings == []


@pytest.mark.integration
def test_validation_gate_blocks_bad_baseline_in_strict_mode() -> None:
    """Strict ValidationGate blocks baseline with insufficient samples and low quality score."""
    gate = ValidationGate(
        collector=BaselineCollector(),
        validator=StatisticalValidator(min_sample_size=30, max_cv=0.30),
        power_analyzer=PowerAnalyzer(target_power=0.80),
        stability_checker=StabilityChecker(),
    )
    bad = BaselineData(
        metric_name="latency_ms",
        values=[100.0, 110.0, 105.0],
        timestamps=[datetime.now(UTC)] * 3,
    )

    result = gate.validate_baseline(bad, effect_size_percent=10.0, strict=True)

    assert result.passed is False
    assert result.can_proceed_with_warning is False
    assert result.quality_score < 50.0
    assert "FAILED" in result.summary
    assert "Insufficient samples" in result.summary


@pytest.mark.integration
def test_validation_gate_passes_good_baseline_with_high_quality_score() -> None:
    """Strong baseline passes all gates and achieves >=85/100 quality score."""
    gate = ValidationGate()
    result = gate.validate_baseline(_good_baseline(n=200), effect_size_percent=10.0, strict=True)

    assert result.passed is True
    assert result.quality_score >= 85.0
    assert "PASSED" in result.summary


@pytest.mark.integration
def test_validation_gate_non_strict_allows_warning_only_baseline() -> None:
    """Non-strict mode sets can_proceed_with_warning=True when no hard errors occur."""
    gate = ValidationGate(
        validator=StatisticalValidator(min_sample_size=30, max_cv=0.30),
        power_analyzer=PowerAnalyzer(target_power=0.80),
        stability_checker=StabilityChecker(deploy_tracker=_StubTracker([{"id": "DEP-1"}])),
    )
    result = gate.validate_baseline(_good_baseline(n=200), effect_size_percent=10.0, strict=False)

    assert result.passed is False  # Deploy occurred during window
    assert result.validation.valid is True
    assert result.can_proceed_with_warning is True


@pytest.mark.integration
def test_ab_test_auto_validate_blocks_bad_and_allows_good_baseline() -> None:
    """ABTest.start(auto_validate=True) blocks flawed baseline and starts on valid baseline."""
    design = ExperimentDesign(
        id="experiment-val-01",
        hypothesis=ExperimentHypothesis(
            primary_metric="latency_ms",
            baseline_value=100.0,
            expected_value=90.0,
            minimum_detectable_effect=0.10,
        ),
    )

    bad_test = ABTest(design)
    bad_baseline = BaselineData(metric_name="latency_ms", values=[100.0, 200.0, 50.0])
    with pytest.raises(ValueError, match="Baseline validation failed"):
        bad_test.start(baseline=bad_baseline, auto_validate=True, strict_validation=True)
    assert bad_test.status == ExperimentStatus.DRAFT

    good_test = ABTest(design)
    good_test.start(baseline=_good_baseline(n=200), auto_validate=True, strict_validation=True)
    assert good_test.status == ExperimentStatus.RUNNING
    assert good_test.baseline_gate_result is not None
    assert good_test.baseline_gate_result.passed is True


@pytest.mark.integration
def test_validated_experiment_runner_enforces_strict_and_non_strict_modes() -> None:
    """ValidatedExperimentRunner gates ABTest start cleanly in strict & warning modes."""
    design = ExperimentDesign(
        id="experiment-runner-01",
        hypothesis=ExperimentHypothesis(
            primary_metric="latency_ms",
            baseline_value=100.0,
            expected_value=90.0,
            minimum_detectable_effect=0.10,
        ),
    )
    runner = ValidatedExperimentRunner(
        validation_gate=ValidationGate(
            stability_checker=StabilityChecker(deploy_tracker=_StubTracker([{"id": "DEP-9"}]))
        )
    )

    strict_exp = ABTest(design)
    with pytest.raises(ValueError, match="Baseline validation failed"):
        runner.start_experiment(strict_exp, _good_baseline(n=200), strict_validation=True)
    assert strict_exp.status == ExperimentStatus.DRAFT

    warn_exp = ABTest(design)
    gate_res = runner.start_experiment(warn_exp, _good_baseline(n=200), strict_validation=False)
    assert warn_exp.status == ExperimentStatus.RUNNING
    assert gate_res is not None and gate_res.can_proceed_with_warning is True


@pytest.mark.integration
def test_cli_validate_baseline_from_file_pass_and_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs experiment validate-baseline` exits 0 on valid baseline and 1 on invalid."""
    good_file = _good_baseline(n=200).save(tmp_path / "good_baseline.json")
    report_out = tmp_path / "gate_report.json"

    rc_good = cli_main(
        [
            "experiment",
            "validate-baseline",
            str(good_file),
            "--effect-size",
            "10",
            "--output",
            str(report_out),
        ]
    )
    assert rc_good == 0
    assert "PASSED" in capsys.readouterr().out
    assert report_out.exists()
    report_payload = json.loads(report_out.read_text(encoding="utf-8"))
    assert report_payload["passed"] is True
    assert report_payload["quality_score"] >= 85.0

    bad_file = BaselineData(metric_name="latency_ms", values=[100.0, 250.0, 50.0]).save(
        tmp_path / "bad_baseline.json"
    )
    rc_bad = cli_main(["experiment", "validate-baseline", str(bad_file)])
    assert rc_bad == 1
    assert "FAILED" in capsys.readouterr().out


@pytest.mark.integration
def test_cli_validate_baseline_from_traces_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs experiment validate-baseline --traces` extracts and validates OTel spans."""
    rng = np.random.default_rng(123)
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    spans = [
        {
            "operation": "initiate_transfer",
            "duration_ms": float(val),
            "status": "ok",
            "timestamp": (t0 + timedelta(minutes=i)).isoformat(),
        }
        for i, val in enumerate(rng.normal(100.0, 10.0, 200))
    ]
    trace_file = tmp_path / "traces.json"
    trace_file.write_text(json.dumps({"spans": spans}), encoding="utf-8")

    rc = cli_main(
        [
            "experiment",
            "validate-baseline",
            "--traces",
            str(trace_file),
            "--metric",
            "latency_ms",
            "--effect-size",
            "10",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASSED" in out
    assert "Quality Score" in out

"""
Unit tests for hard cost budget guards, time-window limits, alerts,
emergency kill switch, Harness pre-flight enforcement, and CLI flags (EVO-007).
"""

import logging
import time
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.cli.main import main as cli_main
from product_value_system.harness.budget import (
    Budget,
    BudgetExceededError,
    BudgetExhausted,
    EmergencyStopTriggered,
)
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness
from product_value_system.observability.tracing import _set_global_tracer_provider


@pytest.mark.unit
class TestBudgetEnforcement:
    """Test per-run, token, daily, monthly, alert, and emergency-stop guards."""

    def test_per_run_budget_enforced(self) -> None:
        """Per-run USD budget blocks projected overspend with BudgetExhausted."""
        budget = Budget(max_usd=1.0)

        budget.check_before_spend(0.5)
        budget.spend(0.5)
        assert budget.spent_usd == pytest.approx(0.5)
        assert budget.is_exhausted() is False

        with pytest.raises(BudgetExhausted, match="Per-run budget exhausted"):
            budget.check_before_spend(0.6)

        # Backward compatibility: BudgetExhausted is also a BudgetExceededError
        with pytest.raises(BudgetExceededError):
            budget.check_before_spend(0.6)

    def test_token_budget_enforced(self) -> None:
        """Token limit blocks projected token exhaustion."""
        budget = Budget(max_tokens=1000)

        budget.check_before_spend(estimated_tokens=400)
        budget.spend(0.01, tokens=600)
        assert budget.spent_tokens == 600
        assert budget.is_exhausted() is False

        with pytest.raises(BudgetExhausted, match="Token budget exhausted"):
            budget.check_before_spend(estimated_tokens=500)

        budget.spend(0.01, tokens=400)
        assert budget.is_exhausted() is True

    def test_daily_budget_enforced_and_persisted(self, tmp_path: Path) -> None:
        """Daily spend persists across Budget instances in SQLite and blocks over-limit."""
        db_file = tmp_path / "budget.db"
        budget_run_1 = Budget(daily_limit_usd=5.0, db_path=db_file)

        for _ in range(4):
            budget_run_1.check_before_spend(1.0)
            budget_run_1.spend(1.0)

        # Fresh Budget instance pointing to same DB sees cumulative daily spend
        budget_run_2 = Budget(daily_limit_usd=5.0, db_path=db_file)
        assert budget_run_2._get_daily_spend() == pytest.approx(4.0)

        with pytest.raises(BudgetExhausted, match="Daily budget exhausted"):
            budget_run_2.check_before_spend(2.0)

    def test_monthly_budget_enforced_and_persisted(self, tmp_path: Path) -> None:
        """Monthly spend persists across Budget instances in SQLite and blocks over-limit."""
        db_file = tmp_path / "budget.db"
        budget_run_1 = Budget(monthly_limit_usd=20.0, db_path=db_file)

        budget_run_1.check_before_spend(12.0)
        budget_run_1.spend(12.0)

        budget_run_2 = Budget(monthly_limit_usd=20.0, db_path=db_file)
        assert budget_run_2._get_monthly_spend() == pytest.approx(12.0)
        assert budget_run_2.remaining()["monthly_usd"] == pytest.approx(8.0)

        with pytest.raises(BudgetExhausted, match="Monthly budget exhausted"):
            budget_run_2.check_before_spend(9.0)

    def test_alert_at_80_percent_threshold_fires_once_and_emits_otel_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """80% threshold triggers warning, callback, and OTel budget.alert event once."""
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        _set_global_tracer_provider(provider)

        alerts: list[tuple[float, float, float]] = []

        def on_alert(spent: float, limit: float, fraction: float) -> None:
            alerts.append((spent, limit, fraction))

        budget = Budget(max_usd=10.0, alert_threshold=0.80, alert_callback=on_alert)

        with provider.get_tracer(__name__).start_as_current_span("budget-test"):
            with caplog.at_level(logging.WARNING):
                budget.check_before_spend(5.0)
                budget.spend(5.0)
                assert len(alerts) == 0

                budget.check_before_spend(3.0)
                budget.spend(3.0)
                # Subsequent check under limit must not re-fire alert
                budget.check_before_spend(0.5)

        assert len(alerts) == 1
        assert alerts[0][2] >= 0.80
        assert budget.alerted is True
        assert "Budget alert: 80.0%" in caplog.text

        spans = exporter.get_finished_spans()
        events = [e for s in spans for e in s.events if e.name == "budget.alert"]
        assert len(events) == 1
        assert dict(events[0].attributes or {})["budget.usage_fraction"] >= 0.80

    def test_emergency_stop_blocks_and_recovers_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EMERGENCY_STOP=1 halts all operations; unsetting restores operation."""
        budget = Budget(max_usd=100.0)

        monkeypatch.setenv("EMERGENCY_STOP", "1")
        with pytest.raises(EmergencyStopTriggered, match="EMERGENCY_STOP=1"):
            budget.check_before_spend(0.01)

        monkeypatch.delenv("EMERGENCY_STOP", raising=False)
        budget.check_before_spend(0.01)

    def test_from_config_file_loads_defaults(self, tmp_path: Path) -> None:
        """Budget.from_config_file loads limits from config/budget_limits.yaml."""
        repo_root = Path(__file__).resolve().parents[2]
        budget = Budget.from_config_file(
            repo_root / "config" / "budget_limits.yaml",
            db_path=tmp_path / "budget.db",
        )
        assert budget.max_usd == pytest.approx(10.0)
        assert budget.daily_limit_usd == pytest.approx(50.0)
        assert budget.monthly_limit_usd == pytest.approx(1000.0)
        assert budget.alert_threshold == pytest.approx(0.80)

    def test_graceful_degradation_on_sqlite_error(self, tmp_path: Path) -> None:
        """SQLite errors during spend persistence log a warning without crashing."""
        corrupt_db = tmp_path / "not_a_db"
        corrupt_db.mkdir()  # Passing a directory path forces sqlite3.OperationalError

        budget = Budget(max_usd=10.0, daily_limit_usd=50.0, db_path=corrupt_db)
        budget.check_before_spend(1.0)
        budget.spend(1.0)
        assert budget.spent_usd == pytest.approx(1.0)

    def test_budget_check_latency_under_1ms(self) -> None:
        """Pre-flight budget checks complete well under 1ms."""
        budget = Budget(max_usd=100.0)
        start = time.perf_counter()
        for _ in range(200):
            budget.check_before_spend(0.01)
        avg_ms = ((time.perf_counter() - start) / 200) * 1000
        assert avg_ms < 1.0, f"Budget check too slow: {avg_ms:.3f}ms"


@pytest.mark.unit
class TestHarnessAndCLIBudgetGuards:
    """Test pre-flight budget and emergency-stop guards in Harness and CLI."""

    @pytest.mark.asyncio
    async def test_harness_preflight_blocks_over_budget_call(self) -> None:
        """Harness.invoke_model raises BudgetExhausted BEFORE invoking model adapter."""
        adapter = MockModelAdapter(responses=["SHOULD NOT BE CALLED"])
        harness = Harness(
            context=ExecutionContext(run_id="budget-preflight-001"),
            model_adapter=adapter,
            budget=Budget(max_usd=0.01),
        )

        with pytest.raises(BudgetExhausted):
            await harness.invoke_model(
                [{"role": "user", "content": "Generate a long specification"}],
                max_tokens=2000,
            )

        assert len(harness.violations) == 1

    @pytest.mark.asyncio
    async def test_harness_emergency_stop_blocks_model_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Harness.invoke_model raises EmergencyStopTriggered when EMERGENCY_STOP=1."""
        monkeypatch.setenv("EMERGENCY_STOP", "1")
        harness = Harness(
            context=ExecutionContext(run_id="budget-estop-001"),
            model_adapter=MockModelAdapter(responses=["SHOULD NOT BE CALLED"]),
            budget=Budget(max_usd=100.0),
        )

        with pytest.raises(EmergencyStopTriggered):
            await harness.invoke_model([{"role": "user", "content": "Hello"}])

    def test_cli_emergency_stop_fails_immediately(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """pvs run exits non-zero immediately when EMERGENCY_STOP=1."""
        monkeypatch.setenv("EMERGENCY_STOP", "1")
        monkeypatch.setenv("PVS_DB_PATH", str(tmp_path / "pvs.db"))
        monkeypatch.setenv("PVS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

        repo_root = Path(__file__).resolve().parents[2]
        problem_file = repo_root / "examples" / "greenfield" / "problem_payment_flow.yaml"
        exit_code = cli_main(["run", str(problem_file)])
        assert exit_code == 1

    def test_cli_max_cost_too_low_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """pvs run --max-cost 0.01 fails pre-flight budget guard."""
        monkeypatch.delenv("EMERGENCY_STOP", raising=False)
        monkeypatch.setenv("PVS_DB_PATH", str(tmp_path / "pvs.db"))
        monkeypatch.setenv("PVS_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

        repo_root = Path(__file__).resolve().parents[2]
        problem_file = repo_root / "examples" / "greenfield" / "problem_payment_flow.yaml"
        exit_code = cli_main(["run", str(problem_file), "--max-cost", "0.01"])
        assert exit_code == 1

"""
Unit tests for EVO-019 BaselineData and BaselineCollector (traces, logs, JSON/JSONL/YAML).
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from product_value_system.validation import BaselineCollector, BaselineData


@dataclass
class _StubTrace:
    duration_ms: float
    status: str
    timestamp: datetime
    attributes: dict[str, Any]


@dataclass
class _StubLog:
    timestamp: datetime
    fields: dict[str, Any]


class _StubBackend:
    def __init__(self, traces: list[Any], logs: list[Any]) -> None:
        self._traces = traces
        self._logs = logs

    def query_traces(
        self,
        *,
        start_time: datetime,  # noqa: ARG002
        end_time: datetime,  # noqa: ARG002
        filters: dict[str, str] | None = None,  # noqa: ARG002
    ) -> list[Any]:
        return list(self._traces)

    def query_logs(
        self,
        *,
        query: str,  # noqa: ARG002
        start_time: datetime,  # noqa: ARG002
        end_time: datetime,  # noqa: ARG002
    ) -> list[Any]:
        return list(self._logs)


@pytest.mark.unit
def test_baseline_data_statistics_and_edge_cases() -> None:
    """Verify mean, std, cv, duration, and safe handling of empty / single-value datasets."""
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    t1 = t0 + timedelta(days=5)
    b = BaselineData(metric_name="latency_ms", values=[100.0, 120.0, 80.0], timestamps=[t0, t0, t1])
    assert b.sample_size == 3
    assert b.mean == pytest.approx(100.0)
    assert b.std == pytest.approx(20.0)
    assert b.cv == pytest.approx(0.20)
    assert b.duration == timedelta(days=5)
    assert b.is_binary is False

    empty = BaselineData(metric_name="latency_ms", values=[])
    assert empty.mean == 0.0 and empty.std == 0.0 and empty.cv == 0.0
    assert empty.duration == timedelta(0)

    zero_mean = BaselineData(metric_name="delta", values=[-1.0, 1.0])
    assert zero_mean.cv == float("inf")


@pytest.mark.unit
def test_baseline_data_json_and_yaml_save_load_roundtrip(tmp_path: Path) -> None:
    """BaselineData round-trips cleanly through JSON and YAML files."""
    t0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    original = BaselineData(
        metric_name="latency_ms",
        values=[95.5, 102.0, 99.1],
        timestamps=[t0, t0 + timedelta(minutes=1), t0 + timedelta(minutes=2)],
        metadata={"service": "payment"},
    )
    json_path = original.save(tmp_path / "baseline.json")
    yaml_path = original.save(tmp_path / "baseline.yaml")

    for path in (json_path, yaml_path):
        loaded = BaselineData.load(path)
        assert loaded.metric_name == "latency_ms"
        assert loaded.values == pytest.approx([95.5, 102.0, 99.1])
        assert loaded.sample_size == 3
        assert loaded.metadata["service"] == "payment"


@pytest.mark.unit
def test_baseline_data_detects_binary_rates() -> None:
    """Binary 0/1 conversion and success-rate baselines are flagged as is_binary=True."""
    binary = BaselineData(metric_name="success_rate", values=[1.0, 1.0, 0.0, 1.0])
    assert binary.is_binary is True
    assert binary.mean == pytest.approx(0.75)


@pytest.mark.unit
def test_collector_extracts_latency_tokens_success_and_cost_from_traces() -> None:
    """BaselineCollector extracts latency_ms, tokens, success_rate, and cost_usd from traces."""
    now = datetime.now(UTC)
    traces = [
        _StubTrace(
            duration_ms=110.0,
            status="success",
            timestamp=now,
            attributes={"gen_ai.usage.total_tokens": 250, "gen_ai.cost_usd": 0.02},
        ),
        _StubTrace(
            duration_ms=130.0,
            status="error",
            timestamp=now + timedelta(minutes=5),
            attributes={"gen_ai.usage.total_tokens": 300, "gen_ai.cost_usd": 0.03},
        ),
    ]
    collector = BaselineCollector(_StubBackend(traces=traces, logs=[]))
    end = now + timedelta(hours=1)

    lat = collector.collect_from_traces("latency_ms", now, end)
    tok = collector.collect_from_traces("tokens", now, end)
    suc = collector.collect_from_traces("success_rate", now, end)
    cost = collector.collect_from_traces("cost_usd", now, end)

    assert lat.values == [110.0, 130.0]
    assert tok.values == [250.0, 300.0]
    assert suc.values == [1.0, 0.0]
    assert cost.values == [0.02, 0.03]


@pytest.mark.unit
def test_collector_collects_from_structured_logs() -> None:
    """BaselineCollector extracts numeric fields from structured log queries."""
    now = datetime.now(UTC)
    logs = [
        _StubLog(timestamp=now, fields={"latency_ms": 98.5}),
        _StubLog(timestamp=now + timedelta(seconds=10), fields={"latency_ms": 101.5}),
        _StubLog(timestamp=now + timedelta(seconds=20), fields={"other": 5.0}),
    ]
    collector = BaselineCollector(_StubBackend(traces=[], logs=logs))
    data = collector.collect_from_logs(
        "latency_ms",
        "service=checkout",
        now,
        now + timedelta(hours=1),
    )

    assert data.sample_size == 2
    assert data.mean == pytest.approx(100.0)


@pytest.mark.unit
def test_collector_reads_local_json_and_jsonl_trace_exports(tmp_path: Path) -> None:
    """BaselineCollector loads OpenTelemetry span JSON and JSONL exports with time filtering."""
    t1 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    t3 = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)

    json_file = tmp_path / "spans.json"
    json_file.write_text(
        json.dumps(
            {
                "spans": [
                    {"duration_ms": 100.0, "status": "ok", "timestamp": t1.isoformat()},
                    {"duration_ms": 120.0, "status": "ok", "timestamp": t2.isoformat()},
                    {"duration_ms": 500.0, "status": "ok", "timestamp": t3.isoformat()},
                ]
            }
        ),
        encoding="utf-8",
    )
    collector = BaselineCollector()
    filtered = collector.collect_from_trace_file(
        json_file,
        metric_name="latency_ms",
        start_time=t1,
        end_time=t2,
    )
    assert filtered.values == [100.0, 120.0]

    jsonl_file = tmp_path / "spans.jsonl"
    jsonl_file.write_text(
        "\n".join(
            [
                json.dumps({"duration_ms": 90.0, "status": "ok"}),
                json.dumps({"duration_ms": 110.0, "status": "error"}),
            ]
        ),
        encoding="utf-8",
    )
    from_jsonl = collector.collect_from_trace_file(jsonl_file, metric_name="success_rate")
    assert from_jsonl.values == [1.0, 0.0]


@pytest.mark.unit
def test_collector_without_backend_raises_clear_error() -> None:
    """Querying traces or logs without a configured backend raises ValueError."""
    collector = BaselineCollector()
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="observability_backend"):
        collector.collect_from_traces("latency_ms", now, now)
    with pytest.raises(ValueError, match="observability_backend"):
        collector.collect_from_logs("latency_ms", "q", now, now)


@pytest.mark.unit
def test_baseline_data_load_rejects_non_object_file(tmp_path: Path) -> None:
    """Loading a non-dict JSON/YAML file fails clearly."""
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain an object"):
        BaselineData.load(bad)

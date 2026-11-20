"""
Baseline data container and trace/log/file collectors for pre-experiment validation (EVO-019).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml


def _parse_ts(value: str | datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass
class BaselineData:
    """Collected baseline observations and summary statistics for validation."""

    metric_name: str
    values: list[float]
    timestamps: list[datetime] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sample_size(self) -> int:
        """Number of observations in the baseline."""
        return len(self.values)

    @property
    def mean(self) -> float:
        """Arithmetic mean of baseline observations (0.0 if empty)."""
        if not self.values:
            return 0.0
        return float(np.mean(self.values))

    @property
    def std(self) -> float:
        """Sample standard deviation (ddof=1; 0.0 when fewer than 2 samples)."""
        if len(self.values) < 2:
            return 0.0
        return float(np.std(self.values, ddof=1))

    @property
    def cv(self) -> float:
        """Coefficient of variation (std / |mean|)."""
        if len(self.values) < 2:
            return 0.0
        m = abs(self.mean)
        if m <= 1e-12:
            return float("inf") if self.std > 0.0 else 0.0
        return self.std / m

    @property
    def is_binary(self) -> bool:
        """True when all baseline observations are binary (0/1 conversion/success rates)."""
        if not self.values:
            return False
        return all(v in (0.0, 1.0) for v in self.values)

    @property
    def duration(self) -> timedelta:
        """Time span covered by collected baseline timestamps."""
        if len(self.timestamps) < 2:
            return timedelta(0)
        return max(self.timestamps) - min(self.timestamps)

    def to_dict(self) -> dict[str, Any]:
        """Serialize baseline data to JSON/YAML dictionary."""
        return {
            "metric_name": self.metric_name,
            "values": [float(v) for v in self.values],
            "timestamps": [
                (ts if ts.tzinfo else ts.replace(tzinfo=UTC))
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z")
                for ts in self.timestamps
            ],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineData:
        """Deserialize baseline data from dictionary."""
        values = [float(v) for v in data.get("values", [])]
        raw_ts = data.get("timestamps", [])
        timestamps = [_parse_ts(ts) for ts in raw_ts] if raw_ts else []
        return cls(
            metric_name=str(data.get("metric_name", "metric")),
            values=values,
            timestamps=timestamps,
            metadata=dict(data.get("metadata", {})),
        )

    def save(self, path: str | Path) -> Path:
        """Persist baseline dataset to JSON or YAML."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        if out.suffix.lower() == ".json":
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            out.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: str | Path) -> BaselineData:
        """Load baseline dataset from JSON or YAML file."""
        in_path = Path(path)
        raw = in_path.read_text(encoding="utf-8")
        data = json.loads(raw) if in_path.suffix.lower() == ".json" else yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise ValueError(f"Baseline file must contain an object: {in_path}")
        return cls.from_dict(data)


class BaselineCollector:
    """Collect baseline observations from OpenTelemetry traces, structured logs, or files."""

    def __init__(self, observability_backend: Any | None = None) -> None:
        self.backend = observability_backend

    def collect_from_traces(
        self,
        metric_name: str,
        start_time: datetime,
        end_time: datetime,
        filters: dict[str, str] | None = None,
    ) -> BaselineData:
        """Collect baseline values from an observability backend's traces."""
        if self.backend is None:
            raise ValueError("observability_backend is required to query traces")

        traces = self.backend.query_traces(
            start_time=start_time,
            end_time=end_time,
            filters=filters,
        )
        return self._build_from_trace_records(
            traces=traces,
            metric_name=metric_name,
            metadata={
                "source": "traces",
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "filters": filters or {},
            },
        )

    def collect_from_trace_file(
        self,
        trace_path: str | Path,
        metric_name: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> BaselineData:
        """Collect baseline values from a local JSON/JSONL OpenTelemetry span export."""
        path = Path(trace_path)
        raw = path.read_text(encoding="utf-8").strip()
        records: list[dict[str, Any]] = []
        if path.suffix.lower() == ".jsonl":
            for line in raw.splitlines():
                if line.strip():
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
        else:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and isinstance(parsed.get("spans"), list):
                records = [s for s in parsed["spans"] if isinstance(s, dict)]
            elif isinstance(parsed, list):
                records = [s for s in parsed if isinstance(s, dict)]

        if start_time is not None or end_time is not None:
            filtered: list[dict[str, Any]] = []
            for rec in records:
                ts = _parse_ts(rec.get("timestamp"))
                if start_time is not None and ts < _parse_ts(start_time):
                    continue
                if end_time is not None and ts > _parse_ts(end_time):
                    continue
                filtered.append(rec)
            records = filtered

        return self._build_from_trace_records(
            traces=records,
            metric_name=metric_name,
            metadata={"source": str(path)},
        )

    def _build_from_trace_records(
        self,
        *,
        traces: list[Any],
        metric_name: str,
        metadata: dict[str, Any],
    ) -> BaselineData:
        values: list[float] = []
        timestamps: list[datetime] = []
        base_time = datetime.now(UTC)

        for idx, trace in enumerate(traces):
            val = self._extract_metric(trace, metric_name)
            if val is None:
                continue
            values.append(val)
            raw_ts = (
                trace.get("timestamp")
                if isinstance(trace, dict)
                else getattr(trace, "timestamp", None)
            )
            timestamps.append(
                _parse_ts(raw_ts) if raw_ts is not None else base_time + timedelta(seconds=idx)
            )

        return BaselineData(
            metric_name=metric_name,
            values=values,
            timestamps=timestamps,
            metadata=metadata,
        )

    def _extract_metric(self, trace: Any, metric_name: str) -> float | None:
        """Extract numeric metric from a trace object or span dictionary."""
        if isinstance(trace, dict):
            attrs = trace.get("attributes", {})
            if not isinstance(attrs, dict):
                attrs = {}
            status = str(trace.get("status", "ok")).lower()
            mapping: dict[str, Any] = {
                "latency_ms": trace.get("duration_ms"),
                "tokens": attrs.get("gen_ai.usage.total_tokens", trace.get("tokens")),
                "success_rate": 1.0 if status in {"success", "ok"} else 0.0,
                "conversion_rate": trace.get("conversion_rate", trace.get("converted")),
                "cost_usd": attrs.get("gen_ai.cost_usd", trace.get("cost_usd")),
            }
            raw_val = mapping.get(metric_name, trace.get(metric_name, attrs.get(metric_name)))
        else:
            attrs = getattr(trace, "attributes", {}) or {}
            status = str(getattr(trace, "status", "ok")).lower()
            obj_mapping: dict[str, Any] = {
                "latency_ms": getattr(trace, "duration_ms", None),
                "tokens": attrs.get("gen_ai.usage.total_tokens"),
                "success_rate": 1.0 if status in {"success", "ok"} else 0.0,
                "conversion_rate": attrs.get("conversion_rate"),
                "cost_usd": attrs.get("gen_ai.cost_usd"),
            }
            raw_val = obj_mapping.get(
                metric_name,
                getattr(trace, metric_name, attrs.get(metric_name)),
            )

        if raw_val is None:
            return None
        try:
            return float(raw_val)
        except (TypeError, ValueError):
            return None

    def collect_from_logs(
        self,
        metric_name: str,
        log_query: str,
        start_time: datetime,
        end_time: datetime,
    ) -> BaselineData:
        """Collect baseline values from structured observability logs."""
        if self.backend is None:
            raise ValueError("observability_backend is required to query logs")

        logs = self.backend.query_logs(
            query=log_query,
            start_time=start_time,
            end_time=end_time,
        )
        values: list[float] = []
        timestamps: list[datetime] = []

        for log in logs:
            fields = log.get("fields", {}) if isinstance(log, dict) else getattr(log, "fields", {})
            if isinstance(fields, dict) and metric_name in fields:
                values.append(float(fields[metric_name]))
                raw_ts = (
                    log.get("timestamp")
                    if isinstance(log, dict)
                    else getattr(log, "timestamp", None)
                )
                timestamps.append(_parse_ts(raw_ts))

        return BaselineData(
            metric_name=metric_name,
            values=values,
            timestamps=timestamps,
            metadata={"source": "logs", "log_query": log_query},
        )

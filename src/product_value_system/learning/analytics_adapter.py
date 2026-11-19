"""
Analytics Adapter Protocol and implementations for querying production metrics (EVO-010).

Supports:
- ``SQLAnalyticsAdapter``: Native SQLite (``sqlite:///...``) and optional SQLAlchemy async
  (PostgreSQL, MySQL)
- ``PrometheusAnalyticsAdapter``: HTTP PromQL query integration via ``httpx``
- ``MockAnalyticsAdapter``: Deterministic mock adapter for tests and local demos
"""

import math
import random
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx
from scipy import stats


@dataclass
class MetricResult:
    """Result from an aggregate metric query."""

    metric_name: str
    value: float
    sample_size: int
    timestamp: datetime
    confidence_interval: tuple[float, float] | None = None
    stddev: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimeseriesResult:
    """Timeseries data points for trend analysis."""

    metric_name: str
    timestamps: list[datetime]
    values: list[float]
    granularity: str  # "hourly", "daily", "weekly"


@runtime_checkable
class AnalyticsAdapter(Protocol):
    """
    Protocol for analytics system integration.

    Implementations query aggregate metrics and timeseries from SQL databases,
    Prometheus, custom HTTP APIs, or test mocks.
    """

    async def query_metric(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None = None,
    ) -> MetricResult:
        """Query aggregate metric value for a date window."""
        ...

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,
    ) -> TimeseriesResult:
        """Query bucketed timeseries data for trend analysis."""
        ...


_SAFE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _compute_mean_ci(
    mean: float,
    stddev: float,
    sample_size: int,
    confidence: float = 0.95,
) -> tuple[float, float] | None:
    """Compute two-sided t-distribution confidence interval for the mean."""
    if sample_size <= 1 or stddev <= 0.0:
        return None
    alpha = 1.0 - confidence
    sem = stddev / math.sqrt(sample_size)
    margin = float(stats.t.ppf(1.0 - alpha / 2.0, sample_size - 1)) * sem
    return (round(mean - margin, 4), round(mean + margin, 4))


class SQLAnalyticsAdapter:
    """
    Analytics adapter for SQL databases (SQLite natively; PostgreSQL/MySQL via SQLAlchemy).

    Expected table schema::

        CREATE TABLE metrics (
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            timestamp TEXT NOT NULL,
            dimension_user_segment TEXT,
            dimension_device_type TEXT
        );
    """

    def __init__(self, connection_string: str) -> None:
        self.connection_string = connection_string
        self._is_sqlite = (
            connection_string.startswith("sqlite:///") or connection_string == ":memory:"
        )
        self._sqlite_path: str | None = None
        self._engine: Any = None

        if self._is_sqlite:
            self._sqlite_path = (
                ":memory:"
                if connection_string == ":memory:"
                else connection_string.removeprefix("sqlite:///")
            )
        else:
            from sqlalchemy.ext.asyncio import create_async_engine

            self._engine = create_async_engine(connection_string)

    async def query_metric(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None = None,
    ) -> MetricResult:
        """Query aggregate mean, sample size, stddev, and 95% CI."""
        if self._is_sqlite:
            return self._query_metric_sqlite(metric_name, start_date, end_date, filters)
        return await self._query_metric_sqlalchemy(metric_name, start_date, end_date, filters)

    def _validate_filter_key(self, key: str) -> str:
        if not _SAFE_IDENT_RE.match(key):
            raise ValueError(f"Invalid filter dimension identifier: '{key}'")
        return f"dimension_{key}"

    def _query_metric_sqlite(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None,
    ) -> MetricResult:
        assert self._sqlite_path is not None
        query = (
            "SELECT metric_value FROM metrics "
            "WHERE metric_name = ? AND timestamp >= ? AND timestamp <= ?"
        )
        params: list[Any] = [metric_name, start_date.isoformat(), end_date.isoformat()]

        if filters:
            for key, value in filters.items():
                col = self._validate_filter_key(key)
                query += f" AND {col} = ?"
                params.append(value)

        with sqlite3.connect(self._sqlite_path) as conn:
            rows = conn.execute(query, params).fetchall()

        if not rows:
            raise ValueError(f"No data found for metric: {metric_name}")

        values = [float(r[0]) for r in rows]
        n = len(values)
        mean = sum(values) / n
        if n > 1:
            variance = sum((v - mean) ** 2 for v in values) / (n - 1)
            stddev = math.sqrt(variance)
        else:
            stddev = 0.0

        return MetricResult(
            metric_name=metric_name,
            value=round(mean, 4),
            sample_size=n,
            timestamp=end_date,
            confidence_interval=_compute_mean_ci(mean, stddev, n),
            stddev=round(stddev, 4),
        )

    async def _query_metric_sqlalchemy(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None,
    ) -> MetricResult:
        from sqlalchemy import text

        query = (
            "SELECT AVG(metric_value) AS avg_value, "
            "COUNT(*) AS sample_size, "
            "STDDEV(metric_value) AS stddev "
            "FROM metrics "
            "WHERE metric_name = :metric_name "
            "AND timestamp >= :start_date "
            "AND timestamp <= :end_date"
        )
        params: dict[str, Any] = {
            "metric_name": metric_name,
            "start_date": start_date,
            "end_date": end_date,
        }

        if filters:
            for key, value in filters.items():
                col = self._validate_filter_key(key)
                query += f" AND {col} = :filter_{key}"
                params[f"filter_{key}"] = value

        async with self._engine.connect() as conn:
            result = await conn.execute(text(query), params)
            row = result.mappings().fetchone()

        if not row or row["avg_value"] is None:
            raise ValueError(f"No data found for metric: {metric_name}")

        mean = float(row["avg_value"])
        n = int(row["sample_size"])
        stddev = float(row["stddev"] or 0.0)
        return MetricResult(
            metric_name=metric_name,
            value=round(mean, 4),
            sample_size=n,
            timestamp=end_date,
            confidence_interval=_compute_mean_ci(mean, stddev, n),
            stddev=round(stddev, 4),
        )

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,
    ) -> TimeseriesResult:
        """Query bucketed timeseries values."""
        if self._is_sqlite:
            return self._query_timeseries_sqlite(
                metric_name, start_date, end_date, granularity, filters
            )
        return await self._query_timeseries_sqlalchemy(
            metric_name, start_date, end_date, granularity, filters
        )

    def _query_timeseries_sqlite(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str,
        filters: dict[str, Any] | None,
    ) -> TimeseriesResult:
        assert self._sqlite_path is not None
        bucket_len = 13 if granularity == "hourly" else 10
        query = (
            f"SELECT substr(timestamp, 1, {bucket_len}) AS bucket, "
            "AVG(metric_value) AS avg_value "
            "FROM metrics "
            "WHERE metric_name = ? AND timestamp >= ? AND timestamp <= ?"
        )
        params: list[Any] = [metric_name, start_date.isoformat(), end_date.isoformat()]
        if filters:
            for key, value in filters.items():
                col = self._validate_filter_key(key)
                query += f" AND {col} = ?"
                params.append(value)
        query += " GROUP BY bucket ORDER BY bucket"

        with sqlite3.connect(self._sqlite_path) as conn:
            rows = conn.execute(query, params).fetchall()

        timestamps: list[datetime] = []
        values: list[float] = []
        for bucket_str, avg_val in rows:
            iso = f"{bucket_str}:00:00" if len(str(bucket_str)) == 13 else f"{bucket_str}T00:00:00"
            timestamps.append(datetime.fromisoformat(iso).replace(tzinfo=UTC))
            values.append(round(float(avg_val), 4))

        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=timestamps,
            values=values,
            granularity=granularity,
        )

    async def _query_timeseries_sqlalchemy(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str,
        filters: dict[str, Any] | None,
    ) -> TimeseriesResult:
        from sqlalchemy import text

        granularity_map = {"hourly": "hour", "daily": "day", "weekly": "week"}
        trunc = granularity_map.get(granularity, "day")
        query = (
            "SELECT DATE_TRUNC(:trunc, timestamp) AS bucket, AVG(metric_value) AS avg_value "
            "FROM metrics "
            "WHERE metric_name = :metric_name "
            "AND timestamp >= :start_date AND timestamp <= :end_date "
            "GROUP BY bucket ORDER BY bucket"
        )
        params = {
            "trunc": trunc,
            "metric_name": metric_name,
            "start_date": start_date,
            "end_date": end_date,
        }
        async with self._engine.connect() as conn:
            result = await conn.execute(text(query), params)
            rows = result.mappings().fetchall()

        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=[row["bucket"] for row in rows],
            values=[float(row["avg_value"]) for row in rows],
            granularity=granularity,
        )


class PrometheusAnalyticsAdapter:
    """Analytics adapter querying a Prometheus HTTP API via ``httpx``."""

    def __init__(
        self,
        base_url: str = "http://localhost:9090",
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        client_kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    async def query_metric(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None = None,
    ) -> MetricResult:
        """Query aggregate scalar/vector metric from Prometheus."""
        response = await self._client.get(
            "/api/v1/query",
            params={"query": metric_name, "time": end_date.isoformat()},
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("data", {}).get("result", [])
        if not results:
            raise ValueError(f"No Prometheus data found for metric: {metric_name}")
        val = float(results[0]["value"][1])
        return MetricResult(
            metric_name=metric_name,
            value=val,
            sample_size=len(results),
            timestamp=end_date,
            confidence_interval=(round(val * 0.95, 4), round(val * 1.05, 4)),
        )

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,
    ) -> TimeseriesResult:
        """Query range timeseries from Prometheus."""
        step_map = {"hourly": "3600s", "daily": "86400s", "weekly": "604800s"}
        response = await self._client.get(
            "/api/v1/query_range",
            params={
                "query": metric_name,
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "step": step_map.get(granularity, "86400s"),
            },
        )
        response.raise_for_status()
        results = response.json().get("data", {}).get("result", [])
        timestamps: list[datetime] = []
        values: list[float] = []
        if results:
            for ts, val in results[0].get("values", []):
                timestamps.append(datetime.fromtimestamp(float(ts), tz=UTC))
                values.append(float(val))
        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=timestamps,
            values=values,
            granularity=granularity,
        )

    async def close(self) -> None:
        """Close HTTP client."""
        await self._client.aclose()


class MockAnalyticsAdapter:
    """
    Deterministic mock analytics adapter for testing without external databases.
    """

    def __init__(
        self,
        mock_data: dict[str, float],
        *,
        baseline_data: dict[str, float] | None = None,
        sample_size: int = 1000,
        ci_half_width_ratio: float = 0.05,
        seed: int = 42,
    ) -> None:
        """
        Initialize mock adapter.

        Args:
            mock_data: Current / post-deployment metric values ``{metric_name: value}``
            baseline_data: Optional distinct pre-deployment baseline values
            sample_size: Simulated sample size for statistical calculations
            ci_half_width_ratio: Half-width fraction used to construct 95% CI
            seed: RNG seed for deterministic timeseries variance
        """
        self.mock_data = dict(mock_data)
        self.baseline_data = dict(baseline_data) if baseline_data is not None else None
        self.sample_size = sample_size
        self.ci_half_width_ratio = ci_half_width_ratio
        self._rng = random.Random(seed)

    async def query_metric(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        filters: dict[str, Any] | None = None,
    ) -> MetricResult:
        """Return mock metric value and 95% confidence interval."""
        if filters and filters.get("_window") == "baseline" and self.baseline_data:
            if metric_name in self.baseline_data:
                val = float(self.baseline_data[metric_name])
                margin = abs(val) * self.ci_half_width_ratio
                return MetricResult(
                    metric_name=metric_name,
                    value=val,
                    sample_size=self.sample_size,
                    timestamp=end_date,
                    confidence_interval=(round(val - margin, 4), round(val + margin, 4)),
                )

        if metric_name not in self.mock_data:
            raise ValueError(f"Mock data not found for: {metric_name}")

        value = float(self.mock_data[metric_name])
        margin = abs(value) * self.ci_half_width_ratio
        ci = (round(value - margin, 4), round(value + margin, 4))

        return MetricResult(
            metric_name=metric_name,
            value=value,
            sample_size=self.sample_size,
            timestamp=end_date,
            confidence_interval=ci,
        )

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,
    ) -> TimeseriesResult:
        """Return deterministic daily/hourly/weekly mock timeseries."""
        base_value = float(self.mock_data.get(metric_name, 50.0))
        step = (
            timedelta(hours=1)
            if granularity == "hourly"
            else timedelta(days=7)
            if granularity == "weekly"
            else timedelta(days=1)
        )

        timestamps: list[datetime] = []
        values: list[float] = []
        current = start_date
        while current <= end_date:
            timestamps.append(current)
            values.append(round(base_value * self._rng.uniform(0.95, 1.05), 4))
            current += step

        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=timestamps,
            values=values,
            granularity=granularity,
        )

"""
Production Product Analytics Connectors (PostHog, Amplitude, BigQuery) &
Continuous Discovery Feedback Ingestion closing the ``MeasurementAgent ->
DiscoveryAgent`` loop (EVO-P3-15).
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from product_value_system.learning.analytics_adapter import (
    AnalyticsAdapter,
    MetricResult,
    TimeseriesResult,
)
from product_value_system.secrets.vault_client import resolve_secret
from product_value_system.security.input_validator import InputValidator

_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,3}\)?[\s.-]?\d{3,5}[\s.-]?\d{4}\b")
_SAFE_METRIC_RE = re.compile(r"^[a-zA-Z0-9_.:/-]{1,120}$")


def redact_feedback_pii(text: str) -> str:
    """Scrub obvious customer email/phone PII before ingesting into discovery knowledge."""
    cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    cleaned = _PHONE_RE.sub("[REDACTED_PHONE]", cleaned)
    return cleaned


class PostHogAnalyticsAdapter:
    """PostHog ``/api/projects/{id}/query/`` connector implementing ``AnalyticsAdapter``."""

    def __init__(
        self,
        project_id: str,
        api_key: str | None = None,
        *,
        host: str = "https://app.posthog.com",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved_key = resolve_secret(
            vault_path="product-value-system/posthog",
            key="api_key",
            env_vars=("POSTHOG_API_KEY",),
            explicit=api_key,
        )
        if not resolved_key:
            raise ValueError("PostHog API key required (set POSTHOG_API_KEY or pass api_key)")
        self.project_id = str(project_id)
        client_kwargs: dict[str, Any] = {
            "base_url": host.rstrip("/"),
            "headers": {
                "Authorization": f"Bearer {resolved_key}",
                "Content-Type": "application/json",
            },
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
        if not _SAFE_METRIC_RE.match(metric_name):
            raise ValueError(f"Invalid metric identifier: {metric_name!r}")
        payload = {
            "query": {
                "kind": "TrendsQuery",
                "series": [{"event": metric_name, "math": "total"}],
                "dateRange": {
                    "date_from": start_date.isoformat(),
                    "date_to": end_date.isoformat(),
                },
                "properties": [
                    {"key": k, "value": v, "operator": "exact"}
                    for k, v in (filters or {}).items()
                ],
            }
        }
        resp = await self._client.post(f"/api/projects/{self.project_id}/query/", json=payload)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        series = results[0] if results else {}
        points = [float(x) for x in (series.get("data") or [])]
        agg_val = float(series.get("aggregated_value", sum(points))) if points or series else 0.0
        return MetricResult(
            metric_name=metric_name,
            value=round(agg_val, 4),
            sample_size=max(1, int(series.get("count", len(points) or 1))),
            timestamp=end_date,
            metadata={"provider": "posthog", "project_id": self.project_id},
        )

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,
    ) -> TimeseriesResult:
        interval = {"hourly": "hour", "daily": "day", "weekly": "week"}.get(granularity, "day")
        payload = {
            "query": {
                "kind": "TrendsQuery",
                "interval": interval,
                "series": [{"event": metric_name, "math": "total"}],
                "dateRange": {
                    "date_from": start_date.isoformat(),
                    "date_to": end_date.isoformat(),
                },
                "properties": [
                    {"key": k, "value": v, "operator": "exact"}
                    for k, v in (filters or {}).items()
                ],
            }
        }
        resp = await self._client.post(f"/api/projects/{self.project_id}/query/", json=payload)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        series = results[0] if results else {}
        raw_days = series.get("days") or []
        raw_vals = series.get("data") or []
        timestamps = [
            datetime.fromisoformat(str(d)).replace(tzinfo=UTC)
            if "T" not in str(d)
            else datetime.fromisoformat(str(d))
            for d in raw_days
        ]
        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=timestamps,
            values=[float(v) for v in raw_vals],
            granularity=granularity,
        )

    async def close(self) -> None:
        await self._client.aclose()


class AmplitudeAnalyticsAdapter:
    """Amplitude Events Segmentation REST API adapter implementing ``AnalyticsAdapter``."""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        *,
        base_url: str = "https://amplitude.com",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved_key = resolve_secret(
            vault_path="product-value-system/amplitude",
            key="api_key",
            env_vars=("AMPLITUDE_API_KEY",),
            explicit=api_key,
        )
        resolved_secret = resolve_secret(
            vault_path="product-value-system/amplitude",
            key="secret_key",
            env_vars=("AMPLITUDE_SECRET_KEY",),
            explicit=secret_key,
        )
        if not resolved_key or not resolved_secret:
            raise ValueError("Amplitude api_key and secret_key are required")
        token = base64.b64encode(f"{resolved_key}:{resolved_secret}".encode()).decode("ascii")
        client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "headers": {"Authorization": f"Basic {token}"},
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
        filters: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> MetricResult:
        ts = await self.query_timeseries(metric_name, start_date, end_date)
        total = sum(ts.values)
        return MetricResult(
            metric_name=metric_name,
            value=round(total, 4),
            sample_size=max(1, len(ts.values)),
            timestamp=end_date,
            metadata={"provider": "amplitude"},
        )

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> TimeseriesResult:
        interval = {"hourly": -3600000, "daily": 1, "weekly": 7}.get(granularity, 1)
        resp = await self._client.get(
            "/api/2/events/segmentation",
            params={
                "e": json.dumps({"event_type": metric_name}),
                "start": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
                "i": str(interval),
            },
        )
        resp.raise_for_status()
        body = resp.json().get("data", {})
        x_vals = body.get("xValues") or []
        series_list = body.get("series") or [[]]
        first_series = series_list[0] if series_list else []
        timestamps = [
            datetime.fromisoformat(str(x)).replace(tzinfo=UTC)
            if "T" not in str(x)
            else datetime.fromisoformat(str(x))
            for x in x_vals
        ]
        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=timestamps,
            values=[float(v) for v in first_series],
            granularity=granularity,
        )

    async def close(self) -> None:
        await self._client.aclose()


class BigQueryAnalyticsAdapter:
    """Parameterized BigQuery REST ``queries`` adapter implementing ``AnalyticsAdapter``."""

    def __init__(
        self,
        project_id: str,
        dataset: str,
        table: str = "product_metrics",
        access_token: str | None = None,
        *,
        base_url: str = "https://bigquery.googleapis.com",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved_token = resolve_secret(
            vault_path="product-value-system/bigquery",
            key="access_token",
            env_vars=("BIGQUERY_ACCESS_TOKEN",),
            explicit=access_token,
        )
        if not resolved_token:
            raise ValueError("BigQuery OAuth access token required")
        for ident_name, ident in (("dataset", dataset), ("table", table)):
            if not re.match(r"^[a-zA-Z0-9_-]+$", ident):
                raise ValueError(f"Invalid BigQuery {ident_name}: {ident!r}")
        self.project_id = project_id
        self.dataset = dataset
        self.table = table
        client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "headers": {
                "Authorization": f"Bearer {resolved_token}",
                "Content-Type": "application/json",
            },
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
        filters: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> MetricResult:
        sql = (
            f"SELECT AVG(metric_value) AS avg_val, COUNT(*) AS cnt "
            f"FROM `{self.project_id}.{self.dataset}.{self.table}` "
            "WHERE metric_name = @metric_name AND ts BETWEEN @start_ts AND @end_ts"
        )
        payload = {
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": [
                {
                    "name": "metric_name",
                    "parameterType": {"type": "STRING"},
                    "parameterValue": {"value": metric_name},
                },
                {
                    "name": "start_ts",
                    "parameterType": {"type": "TIMESTAMP"},
                    "parameterValue": {"value": start_date.isoformat()},
                },
                {
                    "name": "end_ts",
                    "parameterType": {"type": "TIMESTAMP"},
                    "parameterValue": {"value": end_date.isoformat()},
                },
            ],
        }
        resp = await self._client.post(
            f"/bigquery/v2/projects/{self.project_id}/queries",
            json=payload,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows") or []
        if not rows:
            raise ValueError(f"No BigQuery rows found for metric '{metric_name}'")
        fields = rows[0].get("f", [])
        avg_val = float(fields[0]["v"])
        sample_size = int(fields[1]["v"])
        return MetricResult(
            metric_name=metric_name,
            value=round(avg_val, 4),
            sample_size=sample_size,
            timestamp=end_date,
            metadata={"provider": "bigquery", "project_id": self.project_id},
        )

    async def query_timeseries(
        self,
        metric_name: str,
        start_date: datetime,
        end_date: datetime,
        granularity: str = "daily",
        filters: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> TimeseriesResult:
        trunc = {"hourly": "HOUR", "daily": "DAY", "weekly": "WEEK"}.get(granularity, "DAY")
        sql = (
            f"SELECT TIMESTAMP_TRUNC(ts, {trunc}) AS bucket, AVG(metric_value) AS avg_val "
            f"FROM `{self.project_id}.{self.dataset}.{self.table}` "
            "WHERE metric_name = @metric_name AND ts BETWEEN @start_ts AND @end_ts "
            "GROUP BY bucket ORDER BY bucket ASC"
        )
        payload = {
            "query": sql,
            "useLegacySql": False,
            "parameterMode": "NAMED",
            "queryParameters": [
                {
                    "name": "metric_name",
                    "parameterType": {"type": "STRING"},
                    "parameterValue": {"value": metric_name},
                },
                {
                    "name": "start_ts",
                    "parameterType": {"type": "TIMESTAMP"},
                    "parameterValue": {"value": start_date.isoformat()},
                },
                {
                    "name": "end_ts",
                    "parameterType": {"type": "TIMESTAMP"},
                    "parameterValue": {"value": end_date.isoformat()},
                },
            ],
        }
        resp = await self._client.post(
            f"/bigquery/v2/projects/{self.project_id}/queries",
            json=payload,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows") or []
        timestamps: list[datetime] = []
        values: list[float] = []
        for row in rows:
            cells = row.get("f", [])
            if len(cells) >= 2:
                timestamps.append(datetime.fromisoformat(str(cells[0]["v"])))
                values.append(float(cells[1]["v"]))
        return TimeseriesResult(
            metric_name=metric_name,
            timestamps=timestamps,
            values=values,
            granularity=granularity,
        )

    async def close(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class FeedbackItem:
    """Normalized customer feedback ingested from Zendesk, Intercom, Slack, or transcripts."""

    feedback_id: str
    source: str  # zendesk, intercom, slack, transcript
    text: str
    customer_segment: str = "general"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryTriggerEvent:
    """Closed-loop trigger generated when analytics regression aligns with customer feedback."""

    metric_name: str
    baseline_value: float
    observed_value: float
    delta_pct: float
    severity: str
    corroborating_feedback_ids: list[str]
    suggested_problem_title: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class ContinuousDiscoveryPipeline:
    """
    Closes the ``MeasurementAgent -> DiscoveryAgent`` loop by ingesting sanitized
    customer feedback and correlating it with live product analytics regressions.
    """

    def __init__(
        self,
        analytics: AnalyticsAdapter,
        *,
        input_validator: InputValidator | None = None,
    ) -> None:
        self.analytics = analytics
        self._validator = input_validator or InputValidator()
        self._feedback: list[FeedbackItem] = []

    def ingest_feedback(self, items: list[FeedbackItem]) -> tuple[int, int]:
        """Sanitize PII and block prompt-injected feedback (returns ``(accepted, rejected)``)."""
        accepted = 0
        rejected = 0
        for item in items:
            check = self._validator.validate(item.text)
            if not check.is_valid:
                rejected += 1
                continue
            sanitized = FeedbackItem(
                feedback_id=item.feedback_id,
                source=item.source,
                text=redact_feedback_pii(item.text),
                customer_segment=item.customer_segment,
                timestamp=item.timestamp,
                metadata=dict(item.metadata),
            )
            self._feedback.append(sanitized)
            accepted += 1
        return accepted, rejected

    @property
    def feedback_items(self) -> list[FeedbackItem]:
        return list(self._feedback)

    async def evaluate_outcome_and_trigger_discovery(
        self,
        *,
        metric_name: str,
        baseline_value: float,
        start_date: datetime,
        end_date: datetime,
        regression_threshold_pct: float = -10.0,
        higher_is_better: bool = True,
        keywords: list[str] | None = None,
    ) -> DiscoveryTriggerEvent | None:
        """Query live metric; if regressed past threshold, correlate feedback & emit trigger."""
        result = await self.analytics.query_metric(metric_name, start_date, end_date)
        if abs(baseline_value) < 1e-9:
            return None
        delta_pct = ((result.value - baseline_value) / abs(baseline_value)) * 100.0
        regressed = (
            delta_pct <= regression_threshold_pct
            if higher_is_better
            else delta_pct >= abs(regression_threshold_pct)
        )
        if not regressed:
            return None

        match_tokens = [k.lower() for k in (keywords or metric_name.replace("_", " ").split())]
        corroborating = [
            fb.feedback_id
            for fb in self._feedback
            if any(tok in fb.text.lower() for tok in match_tokens if len(tok) >= 3)
        ]
        severity = "critical" if abs(delta_pct) >= 25.0 else "high"
        return DiscoveryTriggerEvent(
            metric_name=metric_name,
            baseline_value=round(baseline_value, 4),
            observed_value=round(result.value, 4),
            delta_pct=round(delta_pct, 2),
            severity=severity,
            corroborating_feedback_ids=corroborating,
            suggested_problem_title=(
                f"Investigate {metric_name} regression ({delta_pct:+.1f}%) "
                f"with {len(corroborating)} customer report(s)"
            ),
        )

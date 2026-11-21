"""
Standard OpenTelemetry GenAI client metrics (EVO-021).

Implements:
- ``gen_ai.client.token.usage`` (Histogram, unit: tokens)
- ``gen_ai.client.operation.duration`` (Histogram, unit: seconds)
- ``gen_ai.client.operation.error`` (Counter, unit: errors)
- ``gen_ai.client.operation.cost`` (Counter, unit: USD)
"""

from __future__ import annotations

import threading
from typing import Any

from opentelemetry import metrics


class GenAIMetrics:
    """Standard OpenTelemetry GenAI metrics collector with thread-safe local snapshots."""

    def __init__(self, meter: metrics.Meter | None = None) -> None:
        self.meter = meter or metrics.get_meter("product_value_system.observability.genai", "0.2.0")
        self._lock = threading.RLock()

        self.token_usage = self.meter.create_histogram(
            name="gen_ai.client.token.usage",
            description="Number of tokens used in LLM request",
            unit="tokens",
        )
        self.operation_duration = self.meter.create_histogram(
            name="gen_ai.client.operation.duration",
            description="Duration of LLM operation",
            unit="seconds",
        )
        self.operation_error = self.meter.create_counter(
            name="gen_ai.client.operation.error",
            description="Number of LLM operation errors",
            unit="errors",
        )
        self.operation_cost = self.meter.create_counter(
            name="gen_ai.client.operation.cost",
            description="Total cost of LLM operations",
            unit="USD",
        )

        self._prompt_tokens_total = 0
        self._completion_tokens_total = 0
        self._operations_total = 0
        self._errors_total = 0
        self._cost_usd_total = 0.0
        self._durations: list[float] = []

    def record_token_usage(
        self,
        system: str,
        model: str,
        token_type: str,  # "prompt" or "completion"
        count: int,
    ) -> None:
        """Record prompt or completion token usage to ``gen_ai.client.token.usage``."""
        self.token_usage.record(
            int(count),
            attributes={
                "gen_ai.system": system,
                "gen_ai.request.model": model,
                "gen_ai.token.type": token_type,
            },
        )
        with self._lock:
            if token_type == "prompt":
                self._prompt_tokens_total += int(count)
            elif token_type == "completion":
                self._completion_tokens_total += int(count)

    def record_operation_duration(
        self,
        system: str,
        operation_name: str,
        duration_seconds: float,
        finish_reason: str = "stop",
    ) -> None:
        """Record operation duration in seconds to ``gen_ai.client.operation.duration``."""
        self.operation_duration.record(
            float(duration_seconds),
            attributes={
                "gen_ai.system": system,
                "gen_ai.operation.name": operation_name,
                "gen_ai.response.finish_reason": finish_reason,
            },
        )
        with self._lock:
            self._operations_total += 1
            self._durations.append(float(duration_seconds))

    def record_error(self, system: str, error_type: str) -> None:
        """Increment ``gen_ai.client.operation.error`` for a failed LLM operation."""
        self.operation_error.add(
            1,
            attributes={
                "gen_ai.system": system,
                "error.type": error_type,
            },
        )
        with self._lock:
            self._errors_total += 1

    def record_cost(self, system: str, model: str, cost_usd: float) -> None:
        """Accumulate USD spend in ``gen_ai.client.operation.cost``."""
        self.operation_cost.add(
            float(cost_usd),
            attributes={
                "gen_ai.system": system,
                "gen_ai.request.model": model,
            },
        )
        with self._lock:
            self._cost_usd_total += float(cost_usd)

    def get_snapshot(self) -> dict[str, Any]:
        """Return lightweight in-process snapshot of recorded GenAI metrics."""
        with self._lock:
            return {
                "prompt_tokens_total": self._prompt_tokens_total,
                "completion_tokens_total": self._completion_tokens_total,
                "total_tokens": self._prompt_tokens_total + self._completion_tokens_total,
                "operations_total": self._operations_total,
                "errors_total": self._errors_total,
                "cost_usd_total": round(self._cost_usd_total, 6),
                "mean_duration_seconds": (
                    round(sum(self._durations) / len(self._durations), 6)
                    if self._durations
                    else 0.0
                ),
            }

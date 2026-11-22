"""
Resilience primitives for LLM and external service invocations (Round 4).

Implements AWS Builders' Library resilience patterns (Marc Brooker, "Timeouts,
Retries, and Backoff with Jitter"):
- Full-jitter exponential backoff preventing synchronized retry storms
- Client-side ``RetryTokenBucket`` capping retry amplification under load
- Stateful ``CircuitBreaker`` (CLOSED / OPEN / HALF_OPEN) for fast-fail protection
- Deadline-bounded ``execute_with_resilience`` separating transient vs. terminal errors
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Finite states of a provider circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when an invocation is rejected because the circuit breaker is OPEN."""


def _require_finite(name: str, value: float, *, minimum: float | None = None) -> float:
    """Validate that a numeric parameter is finite (rejecting NaN/Inf)."""
    fval = float(value)
    if not math.isfinite(fval):
        raise ValueError(f"{name} must be a finite number, got {value!r}")
    if minimum is not None:
        return max(minimum, fval)
    return fval


def full_jitter_delay(
    attempt: int,
    *,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 30.0,
    rng: random.Random | None = None,
) -> float:
    """Compute AWS Builders' Library Full Jitter backoff in ``[0, min(max, base * 2**attempt)]``."""
    base = _require_finite("base_delay_seconds", base_delay_seconds, minimum=0.0)
    ceiling = _require_finite("max_delay_seconds", max_delay_seconds, minimum=0.0)
    # Cap exponent at 60 to avoid OverflowError on huge attempt numbers
    exp = min(max(0, int(attempt)), 60)
    cap = min(ceiling, base * (2**exp))
    generator = rng or random
    return float(generator.uniform(0.0, max(0.0, cap)))


def is_transient_error(exc: BaseException) -> bool:
    """Classify retryable transient errors vs. non-retryable client/logic errors."""
    if isinstance(exc, (ValueError, TypeError, KeyError, PermissionError, CircuitOpenError)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    msg = str(exc).lower()
    transient_markers = (
        "429",
        "rate limit",
        "too many requests",
        "timeout",
        "timed out",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "temporarily unavailable",
        "connection reset",
    )
    return any(marker in msg for marker in transient_markers)


class RetryTokenBucket:
    """Client-side retry budget preventing retry amplification during provider outages."""

    def __init__(
        self,
        max_tokens: float = 100.0,
        *,
        retry_cost: float = 5.0,
        success_refill: float = 1.0,
    ) -> None:
        self.max_tokens = _require_finite("max_tokens", max_tokens, minimum=1.0)
        self.tokens = self.max_tokens
        self.retry_cost = _require_finite("retry_cost", retry_cost, minimum=0.1)
        self.success_refill = _require_finite("success_refill", success_refill, minimum=0.0)

    def try_consume_retry(self) -> bool:
        """Consume retry tokens if available; return False when retry budget is exhausted."""
        if self.tokens >= self.retry_cost:
            self.tokens -= self.retry_cost
            return True
        return False

    def record_success(self) -> None:
        """Replenish retry budget upon a successful call."""
        self.tokens = min(self.max_tokens, self.tokens + self.success_refill)


@dataclass
class CircuitBreakerStats:
    """Observable telemetry snapshot for a ``CircuitBreaker``."""

    name: str
    state: str
    consecutive_failures: int
    total_calls: int
    total_failures: int
    total_rejected: int


class CircuitBreaker:
    """Fast-fail circuit breaker protecting downstream LLM/API endpoints."""

    def __init__(
        self,
        name: str = "default",
        *,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 30.0,
        half_open_max_calls: int = 1,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout_seconds = _require_finite(
            "recovery_timeout_seconds", recovery_timeout_seconds, minimum=0.0
        )
        self.half_open_max_calls = max(1, int(half_open_max_calls))
        self._clock = clock or time.monotonic

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = 0
        self.total_calls = 0
        self.total_failures = 0
        self.total_rejected = 0

    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and (self._clock() - self._opened_at) >= self.recovery_timeout_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_in_flight = 0
        return self._state

    def allow_request(self) -> bool:
        """Return True if a call may proceed under the current circuit state."""
        curr = self.state
        if curr == CircuitState.CLOSED:
            return True
        if curr == CircuitState.HALF_OPEN:
            if self._half_open_in_flight < self.half_open_max_calls:
                self._half_open_in_flight += 1
                return True
            self.total_rejected += 1
            return False
        self.total_rejected += 1
        return False

    def record_success(self) -> None:
        self.total_calls += 1
        self._consecutive_failures = 0
        self._half_open_in_flight = 0
        if self._state != CircuitState.CLOSED:
            logger.info("CircuitBreaker '%s' recovered -> CLOSED", self.name)
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self.total_calls += 1
        self.total_failures += 1
        self._consecutive_failures += 1
        self._half_open_in_flight = max(0, self._half_open_in_flight - 1)
        if (
            self._state == CircuitState.HALF_OPEN
            or self._consecutive_failures >= self.failure_threshold
        ):
            self._state = CircuitState.OPEN
            self._opened_at = self._clock()
            logger.warning(
                "CircuitBreaker '%s' tripped -> OPEN (failures=%d)",
                self.name,
                self._consecutive_failures,
            )

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_in_flight = 0

    def get_stats(self) -> CircuitBreakerStats:
        return CircuitBreakerStats(
            name=self.name,
            state=self.state.value,
            consecutive_failures=self._consecutive_failures,
            total_calls=self.total_calls,
            total_failures=self.total_failures,
            total_rejected=self.total_rejected,
        )


async def execute_with_resilience(
    operation: Callable[[], Awaitable[T] | T],
    *,
    circuit_breaker: CircuitBreaker | None = None,
    retry_bucket: RetryTokenBucket | None = None,
    max_attempts: int = 3,
    base_delay_seconds: float = 0.25,
    max_delay_seconds: float = 8.0,
    total_deadline_seconds: float | None = None,
    error_classifier: Callable[[BaseException], bool] = is_transient_error,
    sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> T:
    """Execute ``operation`` with deadline budget, circuit breaker, and full-jitter retries."""
    attempts = max(1, int(max_attempts))
    start_ts = time.monotonic()
    last_exc: Exception | None = None

    for attempt in range(attempts):
        if circuit_breaker is not None and not circuit_breaker.allow_request():
            raise CircuitOpenError(
                f"Circuit breaker '{circuit_breaker.name}' is OPEN; fast-failing request."
            )

        remaining: float | None = None
        if total_deadline_seconds is not None:
            remaining = total_deadline_seconds - (time.monotonic() - start_ts)
            if remaining <= 0:
                raise TimeoutError(
                    f"Operation exceeded total deadline budget ({total_deadline_seconds:.2f}s)"
                )

        try:
            raw = operation()
            if inspect.isawaitable(raw):
                result: T = (
                    await asyncio.wait_for(raw, timeout=remaining)
                    if remaining is not None
                    else await raw
                )
            else:
                result = raw
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            if retry_bucket is not None:
                retry_bucket.record_success()
            return result
        except Exception as exc:
            last_exc = exc
            retryable = error_classifier(exc)
            if circuit_breaker is not None and retryable:
                circuit_breaker.record_failure()

            if not retryable or attempt >= attempts - 1:
                raise

            if retry_bucket is not None and not retry_bucket.try_consume_retry():
                logger.warning("RetryTokenBucket exhausted; aborting further retries.")
                raise

            delay = full_jitter_delay(
                attempt,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
            )
            if total_deadline_seconds is not None:
                left = total_deadline_seconds - (time.monotonic() - start_ts)
                if left <= 0:
                    raise TimeoutError(
                        f"Total deadline ({total_deadline_seconds:.2f}s) exhausted before retry"
                    ) from exc
                delay = min(delay, left)
            if delay > 0:
                await sleep_fn(delay)

    assert last_exc is not None
    raise last_exc

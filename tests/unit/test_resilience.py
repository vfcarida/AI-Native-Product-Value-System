"""Unit tests for Round 4 resilience primitives (CircuitBreaker, RetryTokenBucket, Full Jitter)."""

from __future__ import annotations

import asyncio
import random

import pytest

from product_value_system.harness import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    RetryTokenBucket,
    execute_with_resilience,
    full_jitter_delay,
    is_transient_error,
)


def test_full_jitter_delay_bounded_and_randomized() -> None:
    rng = random.Random(42)
    samples = [
        full_jitter_delay(attempt=3, base_delay_seconds=0.5, max_delay_seconds=3.0, rng=rng)
        for _ in range(50)
    ]
    assert all(0.0 <= s <= 3.0 for s in samples)
    assert len(set(round(s, 6) for s in samples)) > 10


def test_error_classification_transient_vs_terminal() -> None:
    assert is_transient_error(TimeoutError("read timed out")) is True
    assert is_transient_error(RuntimeError("HTTP 429 rate limit exceeded")) is True
    assert is_transient_error(RuntimeError("503 Service temporarily unavailable")) is True
    assert is_transient_error(ValueError("400 invalid prompt schema")) is False
    assert is_transient_error(PermissionError("401 unauthorized")) is False
    assert is_transient_error(CircuitOpenError("open")) is False


def test_circuit_breaker_closed_open_half_open_recovery() -> None:
    clock = {"now": 100.0}
    cb = CircuitBreaker(
        "openai",
        failure_threshold=2,
        recovery_timeout_seconds=10.0,
        half_open_max_calls=1,
        clock=lambda: clock["now"],
    )

    assert cb.state == CircuitState.CLOSED
    assert cb.allow_request() is True
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.allow_request() is False

    # Advance past recovery timeout -> HALF_OPEN allows exactly 1 probe
    clock["now"] = 111.0
    assert cb.state == CircuitState.HALF_OPEN
    assert cb.allow_request() is True
    assert cb.allow_request() is False

    # Probe succeeds -> back to CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED
    assert cb.get_stats().total_rejected == 2


async def test_execute_with_resilience_retries_transient_and_fast_fails_terminal() -> None:
    attempts = {"n": 0}

    async def flaky_op() -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("HTTP 429 Too Many Requests")
        return "ok"

    async def no_sleep(_s: float) -> None:
        return None

    cb = CircuitBreaker("llm", failure_threshold=5)
    bucket = RetryTokenBucket(max_tokens=20.0, retry_cost=5.0)
    res = await execute_with_resilience(
        flaky_op,
        circuit_breaker=cb,
        retry_bucket=bucket,
        max_attempts=4,
        sleep_fn=no_sleep,
    )
    assert res == "ok"
    assert attempts["n"] == 3
    assert cb.state == CircuitState.CLOSED

    # Non-retryable ValueError must fail immediately on attempt 1 without tripping breaker
    terminal_calls = {"n": 0}

    async def bad_request() -> str:
        terminal_calls["n"] += 1
        raise ValueError("Malformed JSON schema")

    with pytest.raises(ValueError, match="Malformed JSON"):
        await execute_with_resilience(
            bad_request,
            circuit_breaker=cb,
            max_attempts=4,
            sleep_fn=no_sleep,
        )
    assert terminal_calls["n"] == 1
    assert cb.state == CircuitState.CLOSED


async def test_execute_with_resilience_enforces_token_bucket_and_deadline() -> None:
    async def always_503() -> str:
        raise RuntimeError("503 overloaded")

    async def no_sleep(_s: float) -> None:
        return None

    # Bucket only allows 1 retry (cost 5 out of 6) -> aborts after 2 total calls
    calls = {"n": 0}

    async def counted_503() -> str:
        calls["n"] += 1
        return await always_503()

    bucket = RetryTokenBucket(max_tokens=6.0, retry_cost=5.0)
    with pytest.raises(RuntimeError, match="503"):
        await execute_with_resilience(
            counted_503,
            retry_bucket=bucket,
            max_attempts=5,
            sleep_fn=no_sleep,
        )
    assert calls["n"] == 2

    # Deadline enforcement aborts slow call
    async def slow_op() -> str:
        await asyncio.sleep(0.2)
        return "late"

    with pytest.raises(TimeoutError):
        await execute_with_resilience(slow_op, total_deadline_seconds=0.03, max_attempts=1)

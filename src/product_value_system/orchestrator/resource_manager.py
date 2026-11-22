"""
Resource manager enforcing concurrency, memory, and RPM rate limits during
parallel agent execution (EVO-028).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from product_value_system.orchestrator.dependency_graph import ResourceRequirements


class ResourceLimitError(ValueError):
    """Raised when a single agent's requirement exceeds total configured capacity."""


@dataclass
class ResourceSnapshot:
    """Point-in-time and peak resource utilization telemetry."""

    current_llm_calls: int
    current_memory_mb: int
    calls_last_minute: int
    peak_llm_calls: int
    peak_memory_mb: int
    total_acquisitions: int
    total_waits: int

    def to_dict(self) -> dict[str, int]:
        return {
            "current_llm_calls": self.current_llm_calls,
            "current_memory_mb": self.current_memory_mb,
            "calls_last_minute": self.calls_last_minute,
            "peak_llm_calls": self.peak_llm_calls,
            "peak_memory_mb": self.peak_memory_mb,
            "total_acquisitions": self.total_acquisitions,
            "total_waits": self.total_waits,
        }


def _extract_requirements(agent_or_req: Any) -> ResourceRequirements:
    if isinstance(agent_or_req, ResourceRequirements):
        return agent_or_req
    req = getattr(agent_or_req, "resource_requirements", None)
    if isinstance(req, ResourceRequirements):
        return req
    return ResourceRequirements()


class ResourceManager:
    """
    Deadlock-free resource admission controller for parallel agent execution.

    Enforces:
    - ``max_concurrent_llm_calls`` concurrent LLM invocations
    - ``max_memory_mb`` aggregate estimated memory budget
    - ``llm_rate_limit_per_minute`` sliding-window rate limit (RPM)
    """

    def __init__(
        self,
        max_concurrent_llm_calls: int = 10,
        max_memory_mb: int = 2000,
        *,
        llm_rate_limit_per_minute: int = 100,
    ) -> None:
        if max_concurrent_llm_calls < 1:
            raise ValueError("max_concurrent_llm_calls must be >= 1")
        if max_memory_mb < 0:
            raise ValueError("max_memory_mb must be >= 0")
        if llm_rate_limit_per_minute < 1:
            raise ValueError("llm_rate_limit_per_minute must be >= 1")

        self.max_concurrent_llm_calls = int(max_concurrent_llm_calls)
        self.max_memory_mb = int(max_memory_mb)
        self.llm_rate_limit_per_minute = int(llm_rate_limit_per_minute)

        self.current_llm_calls = 0
        self.current_memory_mb = 0
        self.peak_llm_calls = 0
        self.peak_memory_mb = 0
        self.total_acquisitions = 0
        self.total_waits = 0

        self._call_timestamps: deque[float] = deque()
        self._condition = asyncio.Condition()

    def _prune_rate_window(self, now: float) -> None:
        cutoff = now - 60.0
        while self._call_timestamps and self._call_timestamps[0] <= cutoff:
            self._call_timestamps.popleft()

    def can_admit(self, requirements: ResourceRequirements) -> bool:
        """Return True if ``requirements`` fit within current free capacity."""
        now = time.monotonic()
        self._prune_rate_window(now)
        fits_llm = (self.current_llm_calls + requirements.llm_calls) <= (
            self.max_concurrent_llm_calls
        )
        fits_mem = (self.current_memory_mb + requirements.memory_mb) <= self.max_memory_mb
        fits_rpm = (
            len(self._call_timestamps) + requirements.llm_calls <= self.llm_rate_limit_per_minute
        )
        return fits_llm and fits_mem and fits_rpm

    async def acquire(self, agent: Any) -> ResourceRequirements:
        """Wait without deadlocking until resources are available, then reserve them."""
        req = _extract_requirements(agent)

        if req.memory_mb > self.max_memory_mb:
            raise ResourceLimitError(
                f"Agent memory requirement ({req.memory_mb} MB) exceeds "
                f"configured max_memory_mb ({self.max_memory_mb} MB)."
            )
        if req.llm_calls > self.max_concurrent_llm_calls:
            raise ResourceLimitError(
                f"Agent LLM concurrency requirement ({req.llm_calls}) exceeds "
                f"max_concurrent_llm_calls ({self.max_concurrent_llm_calls})."
            )
        if req.llm_calls > self.llm_rate_limit_per_minute:
            raise ResourceLimitError(
                f"Agent LLM call count ({req.llm_calls}) exceeds "
                f"llm_rate_limit_per_minute ({self.llm_rate_limit_per_minute})."
            )

        async with self._condition:
            while True:
                now = time.monotonic()
                self._prune_rate_window(now)

                fits_llm = (self.current_llm_calls + req.llm_calls) <= (
                    self.max_concurrent_llm_calls
                )
                fits_mem = (self.current_memory_mb + req.memory_mb) <= self.max_memory_mb
                fits_rpm = (
                    len(self._call_timestamps) + req.llm_calls <= self.llm_rate_limit_per_minute
                )

                if fits_llm and fits_mem and fits_rpm:
                    self.current_llm_calls += req.llm_calls
                    self.current_memory_mb += req.memory_mb
                    for _ in range(req.llm_calls):
                        self._call_timestamps.append(now)
                    self.peak_llm_calls = max(self.peak_llm_calls, self.current_llm_calls)
                    self.peak_memory_mb = max(self.peak_memory_mb, self.current_memory_mb)
                    self.total_acquisitions += 1
                    return req

                self.total_waits += 1
                if fits_llm and fits_mem and not fits_rpm and self._call_timestamps:
                    # Wait only until the oldest timestamp exits the 60s window
                    sleep_for = max(0.01, (self._call_timestamps[0] + 60.0) - now)
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=sleep_for)
                    except TimeoutError:
                        pass
                else:
                    await self._condition.wait()

    async def release(self, agent_or_req: Any) -> None:
        """Release previously acquired resources and wake waiting coroutines."""
        req = _extract_requirements(agent_or_req)
        async with self._condition:
            self.current_llm_calls = max(0, self.current_llm_calls - req.llm_calls)
            self.current_memory_mb = max(0, self.current_memory_mb - req.memory_mb)
            self._condition.notify_all()

    def get_snapshot(self) -> ResourceSnapshot:
        """Return current and peak utilization metrics."""
        self._prune_rate_window(time.monotonic())
        return ResourceSnapshot(
            current_llm_calls=self.current_llm_calls,
            current_memory_mb=self.current_memory_mb,
            calls_last_minute=len(self._call_timestamps),
            peak_llm_calls=self.peak_llm_calls,
            peak_memory_mb=self.peak_memory_mb,
            total_acquisitions=self.total_acquisitions,
            total_waits=self.total_waits,
        )

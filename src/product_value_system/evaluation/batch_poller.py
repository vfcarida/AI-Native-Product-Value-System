"""
Asynchronous batch job poller with configurable interval, timeout, and progress callbacks (EVO-027).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from product_value_system.evaluation.batch_adapters import BatchAdapter, BatchJob

logger = logging.getLogger(__name__)

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "expired", "cancelled"})


class BatchPollTimeoutError(TimeoutError):
    """Raised when batch polling exceeds ``max_poll_duration`` in strict mode."""

    def __init__(
        self,
        message: str,
        *,
        completed_jobs: list[BatchJob],
        pending_jobs: list[BatchJob],
    ) -> None:
        super().__init__(message)
        self.completed_jobs = completed_jobs
        self.pending_jobs = pending_jobs


class BatchPoller:
    """Poll asynchronous provider batch jobs concurrently until terminal state or timeout."""

    def __init__(
        self,
        adapter: BatchAdapter,
        *,
        poll_interval: float = 60.0,
        max_poll_duration: float = 86400.0,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self.adapter = adapter
        self.poll_interval = max(0.0, float(poll_interval))
        self.max_poll_duration = max(0.0, float(max_poll_duration))
        self.on_progress = on_progress

    async def poll(
        self,
        batch_jobs: list[BatchJob],
        *,
        raise_on_timeout: bool = False,
    ) -> list[BatchJob]:
        """Poll all ``batch_jobs`` until every job finishes or ``max_poll_duration`` elapses."""
        if not batch_jobs:
            return []

        start_time = time.monotonic()
        done_ids: set[str] = {
            job.batch_id for job in batch_jobs if job.status in TERMINAL_STATUSES
        }

        while len(done_ids) < len(batch_jobs):
            pending = [j for j in batch_jobs if j.batch_id not in done_ids]
            statuses = await asyncio.gather(
                *(self.adapter.get_batch_status(j.batch_id) for j in pending),
                return_exceptions=True,
            )

            for job, status_res in zip(pending, statuses, strict=True):
                if isinstance(status_res, BaseException):
                    logger.error("Status poll failed for batch %s: %s", job.batch_id, status_res)
                    job.status = "failed"
                    job.error = str(status_res)
                    done_ids.add(job.batch_id)
                    continue

                status_str = str(status_res.get("status", "in_progress"))
                job.status = status_str
                if status_str == "completed":
                    job.completed_at = status_res.get("completed_at")
                    job.result_file_id = status_res.get("output_file_id") or job.result_file_id
                    done_ids.add(job.batch_id)
                    logger.info("Batch %s completed (result=%s)", job.batch_id, job.result_file_id)
                elif status_str in TERMINAL_STATUSES:
                    job.error = str(status_res.get("error") or f"Batch ended with {status_str}")
                    done_ids.add(job.batch_id)
                    logger.error(
                        "Batch %s ended with status=%s: %s",
                        job.batch_id,
                        status_str,
                        job.error,
                    )

            if self.on_progress is not None:
                self.on_progress(len(done_ids), len(batch_jobs))

            if len(done_ids) >= len(batch_jobs):
                break

            elapsed = time.monotonic() - start_time
            if elapsed >= self.max_poll_duration:
                completed_jobs = [j for j in batch_jobs if j.batch_id in done_ids]
                still_pending = [j for j in batch_jobs if j.batch_id not in done_ids]
                for job in still_pending:
                    job.status = "expired"
                    job.error = f"Polling timed out after {elapsed:.2f}s"
                logger.error(
                    "Batch polling timeout after %.2fs (%d/%d completed)",
                    elapsed,
                    len(completed_jobs),
                    len(batch_jobs),
                )
                if raise_on_timeout:
                    raise BatchPollTimeoutError(
                        f"Batch polling timed out after {elapsed:.2f}s",
                        completed_jobs=completed_jobs,
                        pending_jobs=still_pending,
                    )
                break

            sleep_for = min(self.poll_interval, max(0.0, self.max_poll_duration - elapsed))
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

        return list(batch_jobs)

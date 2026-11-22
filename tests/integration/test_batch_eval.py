"""Integration tests for EVO-027 end-to-end batch evaluation pipelines."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from product_value_system.evaluation import (
    DEFAULT_JUDGE_TEMPLATE,
    AnthropicBatchAdapter,
    BatchAdapter,
    BatchEvalRunner,
    BatchJob,
    BatchResult,
    EvalRunner,
    OpenAIBatchAdapter,
)


def _deterministic_score(sample_id: str) -> float:
    """Deterministic score function shared between mocked real-time and batch judges."""
    num = int(re.findall(r"\d+", sample_id)[-1])
    return round(0.55 + ((num % 9) * 0.05), 2)


def _make_golden_samples(count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"sample_{i:04d}",
            "input": f"Design requirement #{i}",
            "expected_output": f"Structured spec #{i}",
            "actual_output": f"Generated spec #{i}",
        }
        for i in range(count)
    ]


class _MockOpenAIBatchServer:
    """In-memory HTTP simulator for OpenAI /v1/files and /v1/batches endpoints."""

    def __init__(
        self,
        *,
        polls_before_complete: int = 2,
        prompt_tokens: int = 1000,
        completion_tokens: int = 200,
    ) -> None:
        self.polls_before_complete = polls_before_complete
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self._file_seq = 0
        self._batch_seq = 0
        self._input_files: dict[str, list[dict[str, Any]]] = {}
        self._output_files: dict[str, str] = {}
        self._batches: dict[str, dict[str, Any]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _extract_jsonl_from_multipart(self, body: bytes) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for raw_line in body.decode("utf-8", errors="ignore").splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                records.append(json.loads(stripped))
        return records

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/files":
            self._file_seq += 1
            file_id = f"file_in_{self._file_seq:04d}"
            self._input_files[file_id] = self._extract_jsonl_from_multipart(request.content)
            return httpx.Response(200, json={"id": file_id, "purpose": "batch"})

        if request.method == "POST" and path == "/v1/batches":
            payload = json.loads(request.content.decode("utf-8"))
            input_file_id = payload["input_file_id"]
            reqs = self._input_files[input_file_id]
            self._batch_seq += 1
            batch_id = f"batch_oai_{self._batch_seq:04d}"
            out_file_id = f"file_out_{self._batch_seq:04d}"

            out_lines: list[str] = []
            for req in reqs:
                cid = req["custom_id"]
                score = _deterministic_score(cid)
                out_lines.append(
                    json.dumps(
                        {
                            "custom_id": cid,
                            "response": {
                                "status_code": 200,
                                "body": {
                                    "choices": [
                                        {
                                            "message": {
                                                "content": json.dumps(
                                                    {
                                                        "score": score,
                                                        "passed": score >= 0.7,
                                                        "reason": "Evaluated via mock batch API",
                                                    }
                                                )
                                            }
                                        }
                                    ],
                                    "usage": {
                                        "prompt_tokens": self.prompt_tokens,
                                        "completion_tokens": self.completion_tokens,
                                    },
                                },
                            },
                        }
                    )
                )
            self._output_files[out_file_id] = "\n".join(out_lines) + "\n"
            self._batches[batch_id] = {
                "id": batch_id,
                "status": "in_progress",
                "output_file_id": out_file_id,
                "polls": 0,
                "total": len(reqs),
            }
            return httpx.Response(
                200,
                json={
                    "id": batch_id,
                    "status": "in_progress",
                    "created_at": 1700000000,
                    "request_counts": {"total": len(reqs), "completed": 0, "failed": 0},
                },
            )

        if request.method == "GET" and path.startswith("/v1/batches/"):
            batch_id = path.split("/")[-1]
            entry = self._batches[batch_id]
            entry["polls"] += 1
            if entry["polls"] >= self.polls_before_complete:
                entry["status"] = "completed"
                return httpx.Response(
                    200,
                    json={
                        "id": batch_id,
                        "status": "completed",
                        "output_file_id": entry["output_file_id"],
                        "completed_at": 1700000600,
                        "request_counts": {
                            "total": entry["total"],
                            "completed": entry["total"],
                            "failed": 0,
                        },
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": batch_id,
                    "status": "in_progress",
                    "request_counts": {"total": entry["total"], "completed": 0, "failed": 0},
                },
            )

        if request.method == "GET" and path.startswith("/v1/files/") and path.endswith("/content"):
            file_id = path.split("/")[-2]
            return httpx.Response(200, text=self._output_files[file_id])

        return httpx.Response(404, json={"error": f"unhandled path: {path}"})


class _MockAnthropicBatchServer:
    """In-memory HTTP simulator for Anthropic /v1/messages/batches endpoints."""

    def __init__(self, *, input_tokens: int = 1000, output_tokens: int = 200) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self._seq = 0
        self._batches: dict[str, dict[str, Any]] = {}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/messages/batches":
            payload = json.loads(request.content.decode("utf-8"))
            reqs = payload["requests"]
            self._seq += 1
            bid = f"msgbatch_{self._seq:04d}"
            lines: list[str] = []
            for req in reqs:
                cid = req["custom_id"]
                score = _deterministic_score(cid)
                lines.append(
                    json.dumps(
                        {
                            "custom_id": cid,
                            "result": {
                                "type": "succeeded",
                                "message": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": json.dumps(
                                                {"score": score, "passed": score >= 0.7}
                                            ),
                                        }
                                    ],
                                    "usage": {
                                        "input_tokens": self.input_tokens,
                                        "output_tokens": self.output_tokens,
                                    },
                                },
                            },
                        }
                    )
                )
            self._batches[bid] = {"polls": 0, "jsonl": "\n".join(lines) + "\n"}
            return httpx.Response(
                200,
                json={"id": bid, "processing_status": "in_progress"},
            )

        if request.method == "GET" and path.endswith("/results"):
            bid = path.split("/")[-2]
            return httpx.Response(200, text=self._batches[bid]["jsonl"])

        if request.method == "GET" and path.startswith("/v1/messages/batches/"):
            bid = path.split("/")[-1]
            entry = self._batches[bid]
            entry["polls"] += 1
            status = "ended" if entry["polls"] >= 2 else "in_progress"
            return httpx.Response(
                200,
                json={
                    "id": bid,
                    "processing_status": status,
                    "ended_at": "2026-11-21T00:10:00Z" if status == "ended" else None,
                    "results_url": f"https://api.anthropic.com/v1/messages/batches/{bid}/results",
                },
            )

        return httpx.Response(404)


async def test_end_to_end_openai_batch_eval_lifecycle(tmp_path: Path) -> None:
    server = _MockOpenAIBatchServer(polls_before_complete=3)
    adapter = OpenAIBatchAdapter("mock-openai-key", transport=server.transport())
    runner = EvalRunner(
        {
            "evaluation": {
                "batch_mode": {
                    "enabled": True,
                    "provider": "openai",
                    "batch_size": 10,
                    "poll_interval_seconds": 0.001,
                    "realtime_threshold_samples": 10,
                }
            }
        },
        batch_adapter=adapter,
        cache_dir=tmp_path / "e2e_oai",
    )

    samples = _make_golden_samples(25)
    summary = await runner.run_eval(samples, model="gpt-4-turbo")

    assert summary.mode == "batch"
    assert summary.sample_count == 25
    assert len(summary.batch_jobs) == 3
    assert all(j.status == "completed" for j in summary.batch_jobs)
    assert summary.cost_summary.succeeded_requests == 25
    assert summary.cost_summary.savings_pct == pytest.approx(50.0)


async def test_end_to_end_anthropic_batch_eval_lifecycle(tmp_path: Path) -> None:
    server = _MockAnthropicBatchServer(input_tokens=1000, output_tokens=200)
    adapter = AnthropicBatchAdapter("mock-anthropic-key", transport=server.transport())
    runner = EvalRunner(
        {
            "evaluation": {
                "batch_mode": {
                    "enabled": True,
                    "provider": "anthropic",
                    "batch_size": 15,
                    "poll_interval_seconds": 0.001,
                    "realtime_threshold_samples": 5,
                }
            }
        },
        batch_adapter=adapter,
        cache_dir=tmp_path / "e2e_anth",
    )

    summary = await runner.run_eval(_make_golden_samples(30), model="claude-3-5-sonnet-20241022")
    assert summary.mode == "batch"
    assert summary.sample_count == 30
    assert len(summary.batch_jobs) == 2
    assert summary.cost_summary.savings_pct == pytest.approx(30.0)
    assert summary.cost_summary.savings_usd > 0


async def test_cost_savings_validation_500_samples(tmp_path: Path) -> None:
    """Validate >=30% (exact 50% OpenAI) cost reduction on a 500-sample golden dataset."""
    server = _MockOpenAIBatchServer(
        polls_before_complete=1,
        prompt_tokens=1000,
        completion_tokens=200,
    )
    adapter = OpenAIBatchAdapter("mock-openai-key", transport=server.transport())
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=50,
        poll_interval=0.001,
        max_concurrent_batches=5,
        cache_dir=tmp_path / "savings_500",
        adapter=adapter,
    )

    report = await runner.run_batch_eval_with_report(
        _make_golden_samples(500),
        DEFAULT_JUDGE_TEMPLATE,
        model="gpt-4-turbo",
    )
    cs = report.cost_summary
    assert cs.total_requests == 500
    assert cs.succeeded_requests == 500
    assert cs.savings_pct >= 30.0
    assert cs.savings_pct == pytest.approx(50.0)
    # 500 * 1000 in ($5.00) + 500 * 200 out ($3.00) = $8.00 RT -> $4.00 batch -> $4.00 saved
    assert cs.realtime_cost_usd == pytest.approx(8.0)
    assert cs.batch_cost_usd == pytest.approx(4.0)
    assert cs.savings_usd == pytest.approx(4.0)


async def test_batch_and_realtime_result_equivalence(tmp_path: Path) -> None:
    """Batched evaluation must produce identical scores & pass rates as real-time evaluation."""
    samples = _make_golden_samples(20)

    async def realtime_judge(sample: dict[str, Any]) -> BatchResult:
        score = _deterministic_score(str(sample["id"]))
        return BatchResult(
            custom_id=str(sample["id"]),
            response={"mode": "realtime"},
            content=json.dumps({"score": score, "passed": score >= 0.7}),
            input_tokens=1000,
            output_tokens=200,
        )

    rt_runner = EvalRunner(
        {"evaluation": {"batch_mode": {"enabled": False}}},
        realtime_judge=realtime_judge,
        cache_dir=tmp_path / "eq_rt",
    )
    rt_summary = await rt_runner.run_eval(samples, model="gpt-4-turbo")

    server = _MockOpenAIBatchServer(polls_before_complete=1)
    batch_adapter = OpenAIBatchAdapter("mock-openai-key", transport=server.transport())
    batch_runner = EvalRunner(
        {
            "evaluation": {
                "batch_mode": {
                    "enabled": True,
                    "provider": "openai",
                    "batch_size": 10,
                    "poll_interval_seconds": 0.001,
                    "realtime_threshold_samples": 10,
                }
            }
        },
        batch_adapter=batch_adapter,
        realtime_judge=realtime_judge,
        cache_dir=tmp_path / "eq_batch",
    )
    batch_summary = await batch_runner.run_eval(samples, model="gpt-4-turbo")

    assert rt_summary.mode == "realtime"
    assert batch_summary.mode == "batch"
    assert batch_summary.scores_by_id == rt_summary.scores_by_id
    assert batch_summary.mean_score == pytest.approx(rt_summary.mean_score)
    assert batch_summary.pass_rate == pytest.approx(rt_summary.pass_rate)
    assert batch_summary.passed_count == rt_summary.passed_count
    assert batch_summary.cost_summary.batch_cost_usd == pytest.approx(
        rt_summary.cost_summary.realtime_cost_usd * 0.5
    )


async def test_concurrent_batch_submissions_respect_concurrency_limit(tmp_path: Path) -> None:
    """Submit 6 batches concurrently with max_concurrent_batches=3 and verify parallelism bound."""

    class _ConcurrencyTrackingAdapter(BatchAdapter):
        discount_rate = 0.50

        def __init__(self) -> None:
            self.in_flight = 0
            self.max_seen = 0
            self._seq = 0
            self._outputs: dict[str, str] = {}

        async def submit_batch(self, batch_file: Path) -> BatchJob:
            self.in_flight += 1
            self.max_seen = max(self.max_seen, self.in_flight)
            await asyncio.sleep(0.02)
            self._seq += 1
            bid = f"conc_{self._seq:03d}"
            lines = [ln for ln in batch_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
            out: list[str] = []
            for raw in lines:
                cid = json.loads(raw)["custom_id"]
                out.append(
                    json.dumps(
                        {
                            "custom_id": cid,
                            "response": {
                                "body": {
                                    "choices": [{"message": {"content": '{"score": 0.85}'}}],
                                    "usage": {"prompt_tokens": 200, "completion_tokens": 50},
                                }
                            },
                        }
                    )
                )
            self._outputs[bid] = "\n".join(out) + "\n"
            self.in_flight -= 1
            return BatchJob(
                batch_id=bid,
                provider="openai",
                num_requests=len(lines),
                status="in_progress",
                created_at="2026-11-21T00:00:00+00:00",
            )

        async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
            return {"batch_id": batch_id, "status": "completed", "output_file_id": batch_id}

        async def download_result_file(self, file_id: str) -> str:
            return self._outputs[file_id]

        async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
            return {"id": batch_id, "status": "cancelled"}

    adapter = _ConcurrencyTrackingAdapter()
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=25,
        poll_interval=0.001,
        max_concurrent_batches=3,
        cache_dir=tmp_path / "conc_batches",
        adapter=adapter,
    )
    report = await runner.run_batch_eval_with_report(
        _make_golden_samples(150),
        DEFAULT_JUDGE_TEMPLATE,
    )
    assert len(report.jobs) == 6
    assert len(report.results) == 150
    assert adapter.max_seen >= 2
    assert adapter.max_seen <= 3


async def test_large_dataset_1000_samples_20_batches(tmp_path: Path) -> None:
    """Process 1,000 samples across 20 batches of 50 with exact sample-order preservation."""
    server = _MockOpenAIBatchServer(polls_before_complete=1)
    adapter = OpenAIBatchAdapter("mock-openai-key", transport=server.transport())
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=50,
        poll_interval=0.001,
        max_concurrent_batches=10,
        cache_dir=tmp_path / "large_1000",
        adapter=adapter,
    )

    samples = _make_golden_samples(1000)
    report = await runner.run_batch_eval_with_report(
        samples,
        DEFAULT_JUDGE_TEMPLATE,
        model="gpt-4-turbo",
    )

    assert len(report.jobs) == 20
    assert all(j.status == "completed" for j in report.jobs)
    assert len(report.results) == 1000
    assert [r.custom_id for r in report.results] == [f"sample_{i:04d}" for i in range(1000)]
    assert report.cost_summary.succeeded_requests == 1000
    assert report.cost_summary.savings_pct == pytest.approx(50.0)


@pytest.mark.integration_real
@pytest.mark.skipif(
    os.getenv("RUN_REAL_BATCH_TESTS") != "1" or not os.getenv("OPENAI_API_KEY"),
    reason="Opt-in live OpenAI Batch API test (set RUN_REAL_BATCH_TESTS=1 and OPENAI_API_KEY).",
)
async def test_live_openai_batch_api_submission_and_cancel(tmp_path: Path) -> None:
    adapter = OpenAIBatchAdapter(os.environ["OPENAI_API_KEY"])
    batch_file = tmp_path / "live_probe.jsonl"
    batch_file.write_text(
        json.dumps(
            {
                "custom_id": "live_probe_1",
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "Respond with OK"}],
                    "temperature": 0.0,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    job = await adapter.submit_batch(batch_file)
    assert job.batch_id.startswith("batch_")
    cancelled = await adapter.cancel_batch(job.batch_id)
    assert cancelled.get("id") == job.batch_id

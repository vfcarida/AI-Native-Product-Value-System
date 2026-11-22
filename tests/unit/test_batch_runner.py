"""Unit tests for EVO-027 batch evaluation adapters, poller, runner, and hybrid EvalRunner."""

from __future__ import annotations

import json
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
    BatchPoller,
    BatchPollTimeoutError,
    BatchResult,
    EvalRunner,
    OpenAIBatchAdapter,
    estimate_batch_savings,
)
from product_value_system.evaluation.batch_runner import _parse_single_result_line
from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_samples(count: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"sample_{i:04d}",
            "input": f"Prompt {i}",
            "expected_output": f"Expected {i}",
            "actual_output": f"Actual {i}",
        }
        for i in range(count)
    ]


class _FakeBatchAdapter(BatchAdapter):
    """Minimal deterministic BatchAdapter for unit tests."""

    discount_rate = 0.50

    def __init__(
        self,
        *,
        provider: str = "openai",
        fail_submit: bool = False,
        status_sequence: list[str] | None = None,
        result_lines: dict[str, str] | None = None,
        raise_on_status: bool = False,
    ) -> None:
        self.provider = provider
        self.fail_submit = fail_submit
        self.status_sequence = list(status_sequence or ["completed"])
        self.result_lines = dict(result_lines or {})
        self.raise_on_status = raise_on_status
        self.submitted_files: list[Path] = []
        self.poll_counts: dict[str, int] = {}

    async def submit_batch(self, batch_file: Path) -> BatchJob:
        self.submitted_files.append(batch_file)
        if self.fail_submit:
            raise RuntimeError("Simulated submission outage")
        idx = len(self.submitted_files)
        batch_id = f"batch_{idx:03d}"
        lines = [ln for ln in batch_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if batch_id not in self.result_lines:
            out_records: list[str] = []
            for raw in lines:
                req = json.loads(raw)
                cid = req["custom_id"]
                out_records.append(
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
                                                    {"score": 0.9, "passed": True, "reason": "ok"}
                                                )
                                            }
                                        }
                                    ],
                                    "usage": {"prompt_tokens": 500, "completion_tokens": 100},
                                },
                            },
                        }
                    )
                )
            self.result_lines[batch_id] = "\n".join(out_records) + "\n"
        return BatchJob(
            batch_id=batch_id,
            provider=self.provider,
            num_requests=len(lines),
            status="in_progress",
            created_at="2026-11-21T00:00:00+00:00",
        )

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        if self.raise_on_status:
            raise RuntimeError("Status endpoint unreachable")
        seen = self.poll_counts.get(batch_id, 0)
        self.poll_counts[batch_id] = seen + 1
        status = self.status_sequence[min(seen, len(self.status_sequence) - 1)]
        return {
            "batch_id": batch_id,
            "status": status,
            "output_file_id": f"out_{batch_id}" if status == "completed" else None,
            "completed_at": "2026-11-21T00:05:00+00:00" if status == "completed" else None,
        }

    async def download_result_file(self, file_id: str) -> str:
        batch_id = file_id.replace("out_", "")
        return self.result_lines[batch_id]

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        return {"id": batch_id, "status": "cancelled"}


def test_create_batch_files_jsonl_format(tmp_path: Path) -> None:
    adapter = _FakeBatchAdapter()
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=10,
        cache_dir=tmp_path / "batches",
        adapter=adapter,
    )
    samples = _make_samples(3)
    files = runner._create_batch_files(samples, DEFAULT_JUDGE_TEMPLATE, model="gpt-4-turbo")

    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3

    first = json.loads(lines[0])
    assert first["custom_id"] == "sample_0000"
    assert first["method"] == "POST"
    assert first["url"] == "/v1/chat/completions"
    assert first["body"]["model"] == "gpt-4-turbo"
    assert first["body"]["temperature"] == 0.0
    assert first["body"]["messages"][0]["role"] == "system"
    assert "Prompt 0" in first["body"]["messages"][1]["content"]
    assert "Expected 0" in first["body"]["messages"][1]["content"]
    assert "Actual 0" in first["body"]["messages"][1]["content"]


@pytest.mark.parametrize(
    ("num_samples", "expected_files"),
    [(50, 1), (100, 2), (125, 3)],
)
def test_batch_splitting_counts(
    tmp_path: Path,
    num_samples: int,
    expected_files: int,
) -> None:
    adapter = _FakeBatchAdapter()
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=50,
        cache_dir=tmp_path / f"split_{num_samples}",
        adapter=adapter,
    )
    files = runner._create_batch_files(
        _make_samples(num_samples),
        DEFAULT_JUDGE_TEMPLATE,
        model="gpt-4-turbo",
    )
    assert len(files) == expected_files
    total_lines = sum(
        len([ln for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()])
        for f in files
    )
    assert total_lines == num_samples


def test_result_parsing_openai_and_anthropic_formats() -> None:
    openai_line = {
        "custom_id": "s_openai",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [{"message": {"content": '{"score": 0.95, "passed": true}'}}],
                "usage": {"prompt_tokens": 420, "completion_tokens": 80},
            },
        },
        "error": None,
    }
    parsed_oai = _parse_single_result_line(openai_line)
    assert parsed_oai.custom_id == "s_openai"
    assert parsed_oai.error is None
    assert parsed_oai.input_tokens == 420
    assert parsed_oai.output_tokens == 80
    assert "0.95" in parsed_oai.content

    anthropic_line = {
        "custom_id": "s_anth",
        "result": {
            "type": "succeeded",
            "message": {
                "content": [{"type": "text", "text": '{"score": 0.85, "passed": true}'}],
                "usage": {"input_tokens": 310, "output_tokens": 65},
            },
        },
    }
    parsed_anth = _parse_single_result_line(anthropic_line)
    assert parsed_anth.custom_id == "s_anth"
    assert parsed_anth.error is None
    assert parsed_anth.input_tokens == 310
    assert parsed_anth.output_tokens == 65
    assert "0.85" in parsed_anth.content

    anthropic_err = {
        "custom_id": "s_err",
        "result": {"type": "errored", "error": {"type": "invalid_request_error"}},
    }
    parsed_err = _parse_single_result_line(anthropic_err)
    assert parsed_err.custom_id == "s_err"
    assert parsed_err.error is not None
    assert "invalid_request_error" in parsed_err.error


async def test_batch_submission_error_handling(tmp_path: Path) -> None:
    adapter = _FakeBatchAdapter(fail_submit=True)
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=10,
        poll_interval=0.001,
        cache_dir=tmp_path / "err_batches",
        adapter=adapter,
        fallback_to_realtime=False,
    )
    report = await runner.run_batch_eval_with_report(
        _make_samples(4),
        DEFAULT_JUDGE_TEMPLATE,
        model="gpt-4-turbo",
    )
    assert len(report.jobs) == 1
    assert report.jobs[0].status == "failed"
    assert "Simulated submission outage" in (report.jobs[0].error or "")
    assert report.cost_summary.failed_requests == 4
    assert report.cost_summary.succeeded_requests == 0
    assert all(r.error is not None for r in report.results)


async def test_poller_timeout_graceful_and_strict() -> None:
    stuck_adapter = _FakeBatchAdapter(status_sequence=["in_progress"])
    poller = BatchPoller(stuck_adapter, poll_interval=0.005, max_poll_duration=0.02)

    job_graceful = BatchJob(
        batch_id="batch_slow_1",
        provider="openai",
        num_requests=10,
        status="in_progress",
        created_at="2026-11-21T00:00:00+00:00",
    )
    done = await poller.poll([job_graceful], raise_on_timeout=False)
    assert done[0].status == "expired"
    assert "timed out" in (done[0].error or "").lower()

    job_strict = BatchJob(
        batch_id="batch_slow_2",
        provider="openai",
        num_requests=10,
        status="in_progress",
        created_at="2026-11-21T00:00:00+00:00",
    )
    with pytest.raises(BatchPollTimeoutError) as exc_info:
        await poller.poll([job_strict], raise_on_timeout=True)
    assert len(exc_info.value.pending_jobs) == 1
    assert exc_info.value.pending_jobs[0].batch_id == "batch_slow_2"


async def test_poller_progress_callback_and_status_error() -> None:
    progress_events: list[tuple[int, int]] = []
    adapter = _FakeBatchAdapter(status_sequence=["in_progress", "completed"])
    poller = BatchPoller(
        adapter,
        poll_interval=0.001,
        max_poll_duration=1.0,
        on_progress=lambda done, total: progress_events.append((done, total)),
    )
    job = BatchJob(
        batch_id="batch_prog",
        provider="openai",
        num_requests=5,
        status="in_progress",
        created_at="2026-11-21T00:00:00+00:00",
    )
    await poller.poll([job])
    assert job.status == "completed"
    assert progress_events == [(0, 1), (1, 1)]

    failing_adapter = _FakeBatchAdapter(raise_on_status=True)
    poller_err = BatchPoller(failing_adapter, poll_interval=0.001, max_poll_duration=1.0)
    err_job = BatchJob(
        batch_id="batch_err",
        provider="openai",
        num_requests=5,
        status="in_progress",
        created_at="2026-11-21T00:00:00+00:00",
    )
    await poller_err.poll([err_job])
    assert err_job.status == "failed"
    assert "Status endpoint unreachable" in (err_job.error or "")


def test_estimate_batch_savings_openai_and_anthropic() -> None:
    # OpenAI gpt-4-turbo: $10/1M in, $30/1M out -> 1M in + 200K out = $16.00 RT -> $8.00 batch
    rt, batch, saved, pct = estimate_batch_savings("openai", "gpt-4-turbo", 1_000_000, 200_000)
    assert rt == pytest.approx(16.0)
    assert batch == pytest.approx(8.0)
    assert saved == pytest.approx(8.0)
    assert pct == pytest.approx(50.0)

    # Anthropic claude-3-5-sonnet-20241022: $3/1M in, $15/1M out -> 1M in + 200K out = $6.00 RT
    # 30% discount -> $4.20 batch, $1.80 saved
    rt_a, batch_a, saved_a, pct_a = estimate_batch_savings(
        "anthropic",
        "claude-3-5-sonnet-20241022",
        1_000_000,
        200_000,
    )
    assert rt_a == pytest.approx(6.0)
    assert batch_a == pytest.approx(4.2)
    assert saved_a == pytest.approx(1.8)
    assert pct_a == pytest.approx(30.0)


async def test_openai_adapter_submit_poll_download_cancel(tmp_path: Path) -> None:
    seen_requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-openai-key"
        seen_requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/v1/files":
            return httpx.Response(200, json={"id": "file_input_123"})
        if request.method == "POST" and request.url.path == "/v1/batches":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["input_file_id"] == "file_input_123"
            assert payload["completion_window"] == "24h"
            return httpx.Response(
                200,
                json={
                    "id": "batch_oai_123",
                    "status": "in_progress",
                    "created_at": 1700000000,
                    "request_counts": {"total": 2, "completed": 0, "failed": 0},
                },
            )
        if request.method == "GET" and request.url.path == "/v1/batches/batch_oai_123":
            return httpx.Response(
                200,
                json={
                    "id": "batch_oai_123",
                    "status": "completed",
                    "output_file_id": "file_out_456",
                    "completed_at": 1700000300,
                },
            )
        if request.method == "GET" and request.url.path == "/v1/files/file_out_456/content":
            return httpx.Response(200, text='{"custom_id":"s1","response":{"body":{}}}\n')
        if request.method == "POST" and request.url.path == "/v1/batches/batch_oai_123/cancel":
            return httpx.Response(200, json={"id": "batch_oai_123", "status": "cancelling"})
        return httpx.Response(404, json={"error": "not found"})

    batch_file = tmp_path / "batch.jsonl"
    batch_file.write_text('{"custom_id":"s1"}\n{"custom_id":"s2"}\n', encoding="utf-8")

    adapter = OpenAIBatchAdapter("test-openai-key", transport=httpx.MockTransport(handler))
    job = await adapter.submit_batch(batch_file)
    assert job.batch_id == "batch_oai_123"
    assert job.provider == "openai"
    assert job.num_requests == 2

    status = await adapter.get_batch_status("batch_oai_123")
    assert status["status"] == "completed"
    assert status["output_file_id"] == "file_out_456"

    content = await adapter.download_result_file("file_out_456")
    assert "s1" in content

    cancelled = await adapter.cancel_batch("batch_oai_123")
    assert cancelled["status"] == "cancelling"
    assert seen_requests == [
        ("POST", "/v1/files"),
        ("POST", "/v1/batches"),
        ("GET", "/v1/batches/batch_oai_123"),
        ("GET", "/v1/files/file_out_456/content"),
        ("POST", "/v1/batches/batch_oai_123/cancel"),
    ]


async def test_anthropic_adapter_submit_poll_download_cancel(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-anthropic-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        if request.method == "POST" and request.url.path == "/v1/messages/batches":
            payload = json.loads(request.content.decode("utf-8"))
            reqs = payload["requests"]
            assert len(reqs) == 1
            assert reqs[0]["custom_id"] == "s1"
            assert reqs[0]["params"]["system"] == "You are an evaluation judge."
            assert len(reqs[0]["params"]["messages"]) == 1
            return httpx.Response(
                200,
                json={
                    "id": "msgbatch_001",
                    "processing_status": "in_progress",
                    "created_at": "2026-11-21T00:00:00Z",
                },
            )
        if request.method == "GET" and request.url.path == "/v1/messages/batches/msgbatch_001":
            return httpx.Response(
                200,
                json={
                    "id": "msgbatch_001",
                    "processing_status": "ended",
                    "ended_at": "2026-11-21T00:05:00Z",
                    "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_001/results",
                },
            )
        if (
            request.method == "GET"
            and request.url.path == "/v1/messages/batches/msgbatch_001/results"
        ):
            line = json.dumps(
                {
                    "custom_id": "s1",
                    "result": {
                        "type": "succeeded",
                        "message": {
                            "content": [{"type": "text", "text": '{"score": 0.9}'}],
                            "usage": {"input_tokens": 100, "output_tokens": 20},
                        },
                    },
                }
            )
            return httpx.Response(200, text=line + "\n")
        if (
            request.method == "POST"
            and request.url.path == "/v1/messages/batches/msgbatch_001/cancel"
        ):
            return httpx.Response(
                200,
                json={"id": "msgbatch_001", "processing_status": "canceling"},
            )
        return httpx.Response(404)

    batch_file = tmp_path / "anth.jsonl"
    batch_file.write_text(
        json.dumps(
            {
                "custom_id": "s1",
                "body": {
                    "model": "claude-3-5-sonnet-20241022",
                    "messages": [
                        {"role": "system", "content": "You are an evaluation judge."},
                        {"role": "user", "content": "Grade sample 1"},
                    ],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = AnthropicBatchAdapter("test-anthropic-key", transport=httpx.MockTransport(handler))
    job = await adapter.submit_batch(batch_file)
    assert job.batch_id == "msgbatch_001"
    assert job.provider == "anthropic"

    status = await adapter.get_batch_status("msgbatch_001")
    assert status["status"] == "completed"
    text = await adapter.download_result_file(str(status["output_file_id"]))
    assert "s1" in text

    cancelled = await adapter.cancel_batch("msgbatch_001")
    assert cancelled["processing_status"] == "canceling"


async def test_batch_adapter_retries_transient_429(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("product_value_system.evaluation.batch_adapters.asyncio.sleep", _no_sleep)

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"id": "b1", "status": "completed"})

    adapter = OpenAIBatchAdapter(
        "test-key",
        max_retries=3,
        transport=httpx.MockTransport(handler),
    )
    status = await adapter.get_batch_status("b1")
    assert status["status"] == "completed"
    assert calls["count"] == 2


async def test_batch_runner_fallback_to_realtime_on_failure(tmp_path: Path) -> None:
    adapter = _FakeBatchAdapter(fail_submit=True)
    runner = BatchEvalRunner(
        provider="openai",
        batch_size=10,
        poll_interval=0.001,
        cache_dir=tmp_path / "fb_batches",
        adapter=adapter,
        fallback_to_realtime=True,
    )

    async def realtime_judge(sample: dict[str, Any]) -> BatchResult:
        return BatchResult(
            custom_id=str(sample["id"]),
            response={"source": "realtime_fallback"},
            content=json.dumps({"score": 0.88, "passed": True}),
            input_tokens=1000,
            output_tokens=200,
        )

    report = await runner.run_batch_eval_with_report(
        _make_samples(5),
        DEFAULT_JUDGE_TEMPLATE,
        model="gpt-4-turbo",
        realtime_fallback_fn=realtime_judge,
    )
    assert report.used_realtime_fallback is True
    assert len(report.results) == 5
    assert [r.custom_id for r in report.results] == [f"sample_{i:04d}" for i in range(5)]
    assert report.cost_summary.succeeded_requests == 5
    # Fallback is charged at real-time pricing (0% discount)
    assert report.cost_summary.savings_usd == 0.0
    assert report.cost_summary.batch_cost_usd == report.cost_summary.realtime_cost_usd
    assert report.cost_summary.realtime_cost_usd > 0


async def test_eval_runner_threshold_routing(tmp_path: Path) -> None:
    adapter = _FakeBatchAdapter()

    async def rt_judge(sample: dict[str, Any]) -> BatchResult:
        return BatchResult(
            custom_id=str(sample["id"]),
            response={},
            content=json.dumps({"score": 0.9, "passed": True}),
            input_tokens=500,
            output_tokens=100,
        )

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
        realtime_judge=rt_judge,
        cache_dir=tmp_path / "routing_batches",
    )

    # Below threshold -> real-time
    small_summary = await runner.run_eval(_make_samples(5), model="gpt-4-turbo")
    assert small_summary.mode == "realtime"
    assert len(adapter.submitted_files) == 0
    assert small_summary.cost_summary.savings_pct == 0.0

    # At/above threshold -> batch
    large_summary = await runner.run_eval(_make_samples(12), model="gpt-4-turbo")
    assert large_summary.mode == "batch"
    assert len(adapter.submitted_files) == 2
    assert large_summary.cost_summary.savings_pct == pytest.approx(50.0)


async def test_eval_runner_from_yaml_and_rollback(tmp_path: Path) -> None:
    cfg_path = REPO_ROOT / "config" / "evaluation.yaml"
    assert cfg_path.exists()

    async def rt_judge(sample: dict[str, Any]) -> BatchResult:
        return BatchResult(
            custom_id=str(sample["id"]),
            response={},
            content=json.dumps({"score": 0.95, "passed": True}),
            input_tokens=200,
            output_tokens=50,
        )

    runner = EvalRunner.from_yaml(
        cfg_path,
        realtime_judge=rt_judge,
        cache_dir=tmp_path / "yaml_batches",
    )
    assert runner.batch_enabled is True
    assert runner.provider == "openai"
    assert runner.batch_size == 50
    assert runner.realtime_threshold == 10
    assert runner.fallback_to_realtime is True

    # Small run (<10 samples) works via YAML without any live API key
    small = await runner.run_eval(_make_samples(3))
    assert small.mode == "realtime"
    assert small.pass_rate == 1.0

    # Instant rollback via enabled: false forces real-time even above threshold
    disabled_yaml = tmp_path / "disabled_eval.yaml"
    disabled_yaml.write_text(
        "evaluation:\n  batch_mode:\n    enabled: false\n    realtime_threshold_samples: 5\n",
        encoding="utf-8",
    )
    rollback_runner = EvalRunner.from_yaml(
        disabled_yaml,
        realtime_judge=rt_judge,
        cache_dir=tmp_path / "rb_batches",
    )
    rb_summary = await rollback_runner.run_eval(_make_samples(25))
    assert rb_summary.mode == "realtime"
    assert rb_summary.sample_count == 25


async def test_empty_samples_returns_clean_report(tmp_path: Path) -> None:
    adapter = _FakeBatchAdapter()
    runner = BatchEvalRunner(
        provider="openai",
        cache_dir=tmp_path / "empty_batches",
        adapter=adapter,
    )
    report = await runner.run_batch_eval_with_report([], DEFAULT_JUDGE_TEMPLATE)
    assert report.jobs == []
    assert report.results == []
    assert report.cost_summary.total_requests == 0
    assert adapter.submitted_files == []


def test_evaluation_config_and_docs_zero_secrets() -> None:
    detector = HybridSecretDetector()
    for rel in ("config/evaluation.yaml", "docs/batch-evaluations.md"):
        content = (REPO_ROOT / rel).read_text(encoding="utf-8")
        findings = detector.detect(content)
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"

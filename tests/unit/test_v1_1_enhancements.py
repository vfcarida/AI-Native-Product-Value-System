"""Tests for v1.0/v1.1 enhancements: Rust cedarpy, Async Postgres, Gemini/Azure Batch,
CLI --provider, routing.yaml multi-provider, ks_2samp zero warnings, and lockfile hygiene.
"""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

import product_value_system
from product_value_system.adapters import (
    AzureOpenAIModelAdapter,
    GeminiModelAdapter,
    OllamaModelAdapter,
)
from product_value_system.cli.commands import _resolve_cli_adapter
from product_value_system.cli.main import create_parser
from product_value_system.evaluation.batch_adapters import (
    AzureOpenAIBatchAdapter,
    GeminiBatchAdapter,
)
from product_value_system.evaluation.batch_runner import (
    BatchEvalRunner,
    estimate_batch_savings,
)
from product_value_system.persistence import (
    CostRecord,
    Database,
    EvaluationRecord,
    PolicyDecisionRecord,
    PostgresDatabase,
    PostgresValueStreamRepository,
    RunRecord,
    StageRecord,
    create_database,
)
from product_value_system.policy.engine import Decision, PolicyEngine
from product_value_system.routing.model_router import ModelRouter
from product_value_system.simulation.simulation_validator import SimulationValidator

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Version parity & zero-warning SimulationValidator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_package_version_synced_to_1_0_0_and_ks_2samp_emits_zero_warnings() -> None:
    """Package version matches pyproject.toml and ks_2samp emits zero RuntimeWarnings."""
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert product_value_system.__version__ == pyproject["project"]["version"]
    assert product_value_system.__version__ == "3.0.0"

    validator = SimulationValidator()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = validator.compare_distributions([4.0, 4.0, 5.0, 3.0, 4.0], [4.0, 4.0, 5.0, 3.0])
    assert len(results) == 2
    assert not caught, f"Unexpected warnings from ks_2samp: {[str(w.message) for w in caught]}"


# ---------------------------------------------------------------------------
# 2. Native Rust `cedarpy` PyO3 in-process backend in PolicyEngine
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_policy_engine_uses_in_process_cedarpy_rust_bindings_and_fails_closed_on_nan() -> None:
    """PolicyEngine dispatches to in-process cedarpy when available and still guards NaN."""

    @dataclass
    class _FakeDiag:
        reason: list[str]

    @dataclass
    class _FakeAuthResult:
        allowed: bool
        diagnostics: _FakeDiag

    calls: list[dict[str, Any]] = []

    class _FakeCedarPy:
        @staticmethod
        def is_authorized(
            req: dict[str, Any], policies: str, entities: list[dict[str, Any]]
        ) -> _FakeAuthResult:
            calls.append({"req": req, "has_policies": bool(policies), "entities": len(entities)})
            return _FakeAuthResult(allowed=True, diagnostics=_FakeDiag(reason=["policy0"]))

    engine = PolicyEngine(REPO_ROOT / "policies", cedarpy_module=_FakeCedarPy())
    assert engine.active_backend == "cedarpy_rust"

    decision = engine.authorize(
        principal="discovery-agent",
        action="query",
        resource="knowledge_base",
        context={"environment": "sandbox", "cost_so_far": 0.5, "stage": "discovery"},
    )
    assert decision.decision == Decision.ALLOW
    assert decision.policies_matched == ["policy0"]
    assert len(calls) == 1 and calls[0]["has_policies"] is True

    # Even with permissive cedarpy mock, NaN cost_so_far MUST fail-closed before dispatch!
    nan_decision = engine.authorize(
        principal="discovery-agent",
        action="query",
        resource="knowledge_base",
        context={"environment": "sandbox", "cost_so_far": float("nan"), "stage": "discovery"},
    )
    assert nan_decision.decision == Decision.DENY
    assert len(calls) == 1  # Never even invoked cedarpy on corrupt NaN input


# ---------------------------------------------------------------------------
# 3. Async PostgreSQL Persistence Backend & Factory
# ---------------------------------------------------------------------------


class _FakeAsyncPGConn:
    """In-memory asyncpg-compatible connection supporting runs, stages, and migrations."""

    def __init__(self, store: dict[str, Any]) -> None:
        self.store = store

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield self

    async def execute(self, sql: str, *args: Any) -> str:
        import re

        norm = " ".join(sql.split()).upper()
        if "SCHEMA_MIGRATIONS" in norm:
            versions = [
                int(v)
                for v in re.findall(
                    r"INSERT INTO SCHEMA_MIGRATIONS\s*\(VERSION\)\s*VALUES\s*\((\d+)\)",
                    norm,
                )
            ]
            if versions:
                self.store["migration_version"] = max(
                    self.store.get("migration_version", 0),
                    max(versions),
                )
            elif "CREATE TABLE" in norm:
                self.store["migration_version"] = max(
                    self.store.get("migration_version", 0),
                    1,
                )
            return "CREATE TABLE"
        if norm.startswith("INSERT INTO VALUE_STREAM_RUNS"):
            self.store["runs"][args[0]] = {
                "id": args[0],
                "problem_id": args[1],
                "status": args[2],
                "started_at": args[3],
                "completed_at": args[4],
                "total_cost_usd": args[5],
                "outcome_achieved": args[6],
                "artifact_dir": args[7],
            }
            return "INSERT 0 1"
        if norm.startswith("UPDATE VALUE_STREAM_RUNS"):
            run = self.store["runs"][args[4]]
            run["status"] = args[0]
            run["completed_at"] = args[1]
            run["total_cost_usd"] = args[2]
            run["outcome_achieved"] = args[3]
            return "UPDATE 1"
        if norm.startswith("INSERT INTO STAGES"):
            self.store["stages"].append(
                {
                    "id": args[0],
                    "run_id": args[1],
                    "stage_name": args[2],
                    "agent_name": args[3],
                    "status": args[4],
                    "started_at": args[5],
                    "completed_at": args[6],
                    "artifact_path": args[7],
                    "cost_usd": args[8],
                    "error": args[9],
                    "metadata": args[10],
                }
            )
            return "INSERT 0 1"
        self.store["other_writes"].append((norm[:40], args))
        return "INSERT 0 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        norm = " ".join(sql.split()).upper()
        if norm.startswith("SELECT 1"):
            return {"ok": 1}
        if "FROM SCHEMA_MIGRATIONS" in norm:
            return {"version": self.store.get("migration_version", 0)}
        if "FROM VALUE_STREAM_RUNS WHERE ID" in norm:
            return self.store["runs"].get(args[0])
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        norm = " ".join(sql.split()).upper()
        if "FROM VALUE_STREAM_RUNS" in norm:
            return list(self.store["runs"].values())[: int(args[0])]
        if "FROM STAGES" in norm:
            return [s for s in self.store["stages"] if s["run_id"] == args[0]]
        return []


class _FakeAsyncPGPool:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {"runs": {}, "stages": [], "other_writes": []}
        self.closed = False

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield _FakeAsyncPGConn(self.store)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_database_repository_migrations_and_factory(tmp_path: Path) -> None:
    """PostgresDatabase & PostgresValueStreamRepository round-trip all record types cleanly."""
    # Factory auto-selects SQLite vs PostgreSQL by DSN
    sqlite_db = create_database(tmp_path / "local.db")
    assert isinstance(sqlite_db, Database)

    pool = _FakeAsyncPGPool()
    pg_migrations_dir = REPO_ROOT / "migrations" / "postgres"
    pg_db = create_database(
        "postgresql://pvs:pass@localhost:5432/pvs",
        migrations_dir=pg_migrations_dir,
        pool=pool,
    )
    assert isinstance(pg_db, PostgresDatabase)
    assert await pg_db.health_check() is True

    expected_migrations = len(list(pg_migrations_dir.glob("*.sql")))
    applied = await pg_db.migrate()
    assert applied == expected_migrations
    assert await pg_db.migrate() == 0  # Idempotent second pass

    repo = PostgresValueStreamRepository(pg_db)
    now = datetime.now(UTC)
    run = RunRecord(
        id="run-pg-1",
        problem_id="prob-1",
        status="running",
        started_at=now,
        total_cost_usd=0.12,
        artifact_dir="/tmp/artifacts",
    )
    await repo.save_run(run)
    loaded = await repo.get_run("run-pg-1")
    assert loaded is not None and loaded.status == "running"

    run.status = "completed"
    run.outcome_achieved = True
    run.total_cost_usd = 0.45
    await repo.update_run(run)
    updated = await repo.get_run("run-pg-1")
    assert updated is not None and updated.outcome_achieved is True
    assert len(await repo.list_runs(limit=10)) == 1

    await repo.save_stage(
        StageRecord(
            id="st-1",
            run_id="run-pg-1",
            stage_name="discovery",
            agent_name="discovery-agent",
            status="completed",
            started_at=now,
            cost_usd=0.08,
            metadata={"tokens": 1200},
        )
    )
    stages = await repo.get_stages("run-pg-1")
    assert len(stages) == 1 and stages[0].metadata == {"tokens": 1200}

    await repo.save_policy_decision(
        PolicyDecisionRecord(
            id="pd-1",
            run_id="run-pg-1",
            timestamp=now,
            agent_name="discovery-agent",
            action="query",
            resource="knowledge_base",
            decision="allow",
            policy_matched="policy_permit_discovery_query",
        )
    )
    await repo.save_evaluation(
        EvaluationRecord(
            id="ev-1",
            run_id="run-pg-1",
            stage_id="st-1",
            artifact_type="outcome",
            judge_name="schema",
            judge_type="deterministic",
            score=1.0,
            passed=True,
        )
    )
    await repo.save_cost(
        CostRecord(
            id="c-1",
            run_id="run-pg-1",
            stage_id="st-1",
            timestamp=now,
            agent_name="discovery-agent",
            model_name="gemini-2.5-flash",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.0002,
        )
    )
    assert len(pool.store["other_writes"]) == 3
    await pg_db.close()
    assert pool.closed is True

    with pytest.raises(ValueError, match="postgresql://"):
        PostgresDatabase("mysql://invalid")


# ---------------------------------------------------------------------------
# 4. Gemini & Azure OpenAI Batch Evaluation Runners
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gemini_and_azure_batch_adapters_and_50pct_discount(tmp_path: Path) -> None:
    """GeminiBatchAdapter and AzureOpenAIBatchAdapter run full submit/poll/parse with 50% off."""
    # Gemini Batch mock
    def gemini_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":batchGenerateContent"):
            return httpx.Response(
                200,
                json={
                    "name": "batches/gem-001",
                    "metadata": {"state": "BATCH_STATE_RUNNING"},
                },
            )
        if request.url.path.endswith("/batches/gem-001"):
            return httpx.Response(
                200,
                json={
                    "name": "batches/gem-001",
                    "done": True,
                    "metadata": {"state": "BATCH_STATE_SUCCEEDED"},
                    "response": {"responsesFile": "/files/gem-001-out"},
                },
            )
        if request.url.path.endswith("/files/gem-001-out"):
            jsonl = json.dumps(
                {
                    "key": "s1",
                    "response": {
                        "candidates": [
                            {"content": {"parts": [{"text": '{"score": 0.95, "passed": true}'}]}}
                        ],
                        "usageMetadata": {"promptTokenCount": 400, "candidatesTokenCount": 100},
                    },
                }
            )
            return httpx.Response(200, text=jsonl + "\n")
        return httpx.Response(404)

    gem_adapter = GeminiBatchAdapter(
        "test-gem-key",
        model="gemini-2.5-flash",
        transport=httpx.MockTransport(gemini_handler),
    )
    gem_runner = BatchEvalRunner(
        provider="gemini",
        adapter=gem_adapter,
        poll_interval=0.01,
        cache_dir=tmp_path / "gem_batches",
    )
    gem_report = await gem_runner.run_batch_eval_with_report(
        samples=[{"id": "s1", "input": "q", "expected_output": "a", "actual_output": "a"}],
        judge_prompt_template="Judge: {input} -> {actual_output}",
        model="gemini-2.5-flash",
    )
    assert len(gem_report.results) == 1
    assert '"score": 0.95' in gem_report.results[0].content
    assert gem_report.cost_summary.savings_pct == pytest.approx(50.0)

    # Azure OpenAI Global Batch mock
    def azure_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("api-version") == "2024-10-21"
        if request.url.path == "/openai/files" and request.method == "POST":
            return httpx.Response(200, json={"id": "file-az-1"})
        if request.url.path == "/openai/batches" and request.method == "POST":
            return httpx.Response(200, json={"id": "batch-az-1", "status": "in_progress"})
        if request.url.path == "/openai/batches/batch-az-1":
            return httpx.Response(
                200, json={"id": "batch-az-1", "status": "completed", "output_file_id": "file-out"}
            )
        if request.url.path == "/openai/files/file-out/content":
            jsonl = json.dumps(
                {
                    "custom_id": "s1",
                    "response": {
                        "status_code": 200,
                        "body": {
                            "choices": [{"message": {"content": '{"score": 0.91}'}}],
                            "usage": {"prompt_tokens": 500, "completion_tokens": 120},
                        },
                    },
                }
            )
            return httpx.Response(200, text=jsonl + "\n")
        return httpx.Response(404)

    az_adapter = AzureOpenAIBatchAdapter(
        endpoint="https://acme.openai.azure.com",
        api_key="test-az-key",
        transport=httpx.MockTransport(azure_handler),
    )
    az_runner = BatchEvalRunner(
        provider="azure_openai",
        adapter=az_adapter,
        poll_interval=0.01,
        cache_dir=tmp_path / "az_batches",
    )
    az_report = await az_runner.run_batch_eval_with_report(
        samples=[{"id": "s1", "input": "q", "expected_output": "a", "actual_output": "a"}],
        judge_prompt_template="Judge: {input}",
        model="gpt-4o",
    )
    assert '"score": 0.91' in az_report.results[0].content
    assert az_report.cost_summary.savings_pct == pytest.approx(50.0)

    rt, batch_c, saved, pct = estimate_batch_savings("gemini", "gemini-2.5-pro", 1_000_000, 100_000)
    assert pct == pytest.approx(50.0)
    assert saved == pytest.approx(rt - batch_c)


# ---------------------------------------------------------------------------
# 5. CLI --provider/--model, Multi-Provider routing.yaml & Lockfile Parity
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_provider_flags_multiprovider_routing_and_lockfile_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI parses --provider/--model, routing.yaml routes Gemini/Ollama, and lockfile is valid."""
    parser = create_parser()
    for cmd in (
        ["run", "problem.yaml", "--provider", "gemini", "--model", "gemini-2.5-flash"],
        ["eval", "discovery", "--provider", "ollama", "--model", "llama3.2"],
        ["review", "spec.yaml", "--provider", "azure_openai", "--model", "gpt-4o"],
    ):
        parsed = parser.parse_args(cmd)
        assert parsed.provider in {"gemini", "ollama", "azure_openai"}
        assert parsed.model is not None

    # Verify _resolve_cli_adapter resolves Gemini, Azure OpenAI, Ollama, and Mock cleanly
    monkeypatch.setenv("GEMINI_API_KEY", "test-gem-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://acme.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-az-key")

    gem_args = parser.parse_args(["eval", "discovery", "--provider", "gemini"])
    az_args = parser.parse_args(["eval", "discovery", "--provider", "azure_openai", "--model", "d"])
    ol_args = parser.parse_args(["eval", "discovery", "--provider", "ollama"])
    mk_args = parser.parse_args(["eval", "discovery", "--provider", "mock"])

    assert isinstance(_resolve_cli_adapter(gem_args), GeminiModelAdapter)
    assert isinstance(_resolve_cli_adapter(az_args), AzureOpenAIModelAdapter)
    assert isinstance(_resolve_cli_adapter(ol_args), OllamaModelAdapter)
    assert _resolve_cli_adapter(mk_args) is None

    # Multi-provider routing_multiprovider.yaml with preferred_provider overrides
    router_gem = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing_multiprovider.yaml")
    router_gem.policy.preferred_provider = "gemini"
    d_gem = router_gem.route(
        [{"role": "user", "content": "Extract status codes"}], task_type="extract"
    )
    assert d_gem.provider == "gemini"
    assert d_gem.model == "gemini-2.5-flash"

    router_ol = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing_multiprovider.yaml")
    router_ol.policy.preferred_provider = "ollama"
    d_ol = router_ol.route([{"role": "user", "content": "Format CSV to JSON"}], task_type="format")
    assert d_ol.provider == "ollama"
    assert d_ol.model == "llama3.2"

    # Verify deterministic requirements.lock parity via scripts/generate-lockfile.py --check
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "generate-lockfile.py"), "--check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"Lockfile parity failed:\n{proc.stderr}\n{proc.stdout}"

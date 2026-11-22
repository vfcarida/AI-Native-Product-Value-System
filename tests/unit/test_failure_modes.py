"""Real-world failure-mode & chaos recovery tests (Round 8)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from product_value_system.caching.cache_backends import SQLiteCacheBackend
from product_value_system.caching.prompt_cache import PromptCache
from product_value_system.harness.checkpoint import Checkpoint, CheckpointManager
from product_value_system.orchestrator.checkpoints import (
    CheckpointManager as RunCheckpointManager,
)
from product_value_system.orchestrator.dependency_graph import AgentDefinition, DependencyGraph
from product_value_system.orchestrator.parallel_executor import ParallelExecutor
from product_value_system.orchestrator.state_machine import RunStatus, ValueStreamRun


@pytest.mark.unit
def test_harness_checkpoint_recovers_from_corrupt_json_and_blocks_traversal(tmp_path: Path) -> None:
    """Corrupt checkpoint JSON is quarantined cleanly; sibling checkpoints remain readable."""
    mgr = CheckpointManager(tmp_path / "cp")
    good = Checkpoint(
        run_id="run-good",
        stage_name="discovery",
        agent_name="discovery-agent",
        iteration=1,
        timestamp=datetime.now(UTC),
    )
    mgr.save(good)

    bad_path = tmp_path / "cp" / "run-bad_discovery.json"
    bad_path.write_text("{truncated json...", encoding="utf-8")

    # list_checkpoints skips corrupt sibling without crashing
    listed = mgr.list_checkpoints()
    assert len(listed) == 1
    assert listed[0].run_id == "run-good"

    # load() quarantines corrupt file and returns None cleanly
    assert mgr.load("run-bad", "discovery") is None
    assert not bad_path.exists()
    assert bad_path.with_suffix(".json.corrupt").exists()

    # Path traversal rejected
    with pytest.raises(ValueError, match="Unsafe checkpoint"):
        mgr.load("../../etc/passwd", "discovery")


@pytest.mark.unit
def test_orchestrator_checkpoint_recovers_from_corrupt_json_and_blocks_traversal(
    tmp_path: Path,
) -> None:
    """RunCheckpointManager atomically writes, quarantines corrupt JSON, and blocks traversal."""
    mgr = RunCheckpointManager(tmp_path / "run_cp")
    run = ValueStreamRun(id="run-ok", problem_id="prob-1", status=RunStatus.RUNNING)
    mgr.save_checkpoint(run)
    loaded = mgr.load_checkpoint("run-ok")
    assert loaded is not None and loaded.id == "run-ok"

    bad = tmp_path / "run_cp" / "run-corrupt.json"
    bad.write_text("not-valid-json", encoding="utf-8")
    assert mgr.load_checkpoint("run-corrupt") is None
    assert bad.with_suffix(".json.corrupt").exists()

    with pytest.raises(ValueError, match="Unsafe checkpoint"):
        mgr.load_checkpoint("../escape")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sqlite_cache_quarantines_corrupt_db_and_rebuilds_cleanly(tmp_path: Path) -> None:
    """Corrupted ephemeral SQLite cache file must be quarantined and rebuilt without crashing."""
    db_file = tmp_path / "llm_cache.db"
    db_file.write_bytes(b"THIS_IS_CORRUPTED_GARBAGE_NOT_A_SQLITE_DATABASE_HEADER")

    backend = SQLiteCacheBackend(db_path=db_file, max_entries=100)
    cache = PromptCache(backend=backend, enable_semantic_matching=False)

    msgs = [{"role": "user", "content": "hello chaos"}]
    assert await cache.get(msgs, "gpt-4o", 0.0, 64) is None
    await cache.set(msgs, "gpt-4o", 0.0, 64, {"content": "recovered", "usage": {"total_tokens": 9}})
    hit = await cache.get(msgs, "gpt-4o", 0.0, 64)

    assert hit is not None and hit["content"] == "recovered"
    assert db_file.with_suffix(".db.corrupt").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_parallel_executor_isolates_partial_layer_failure() -> None:
    """Failure in one parallel agent must not cancel or corrupt peer agents in the same layer."""
    graph = DependencyGraph()
    graph.add_agent(AgentDefinition(name="arch_review"))
    graph.add_agent(AgentDefinition(name="sec_review"))

    async def failing_agent(_inputs: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("Upstream 503 outage")

    async def healthy_agent(_inputs: dict[str, Any]) -> dict[str, Any]:
        return {"status": "approved"}

    executor = ParallelExecutor(max_concurrent=2)
    results = await executor.execute(
        dependency_graph=graph,
        initial_inputs={},
        agent_registry={"arch_review": failing_agent, "sec_review": healthy_agent},
    )

    assert results["arch_review"].success is False
    assert isinstance(results["arch_review"].error, RuntimeError)
    assert results["sec_review"].success is True
    assert results["sec_review"].output == {"status": "approved"}

"""
Persistence layer tests (P015).
"""

import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_value_system.persistence.database import Database
from product_value_system.persistence.models import (
    CostRecord,
    EvaluationRecord,
    PolicyDecisionRecord,
    RunRecord,
    StageRecord,
)
from product_value_system.persistence.repository import ValueStreamRepository


@pytest.fixture
def test_db(tmp_path: Path) -> Generator[Database, None, None]:
    """Test database."""
    db_path = tmp_path / "test.db"
    migrations_dir = Path(__file__).parent.parent.parent / "migrations"

    db = Database(db_path, migrations_dir)
    db.connect()
    db.migrate()

    yield db

    db.close()


@pytest.fixture
def repository(test_db: Database) -> ValueStreamRepository:
    """Repository fixture."""
    return ValueStreamRepository(test_db)


@pytest.mark.unit
def test_database_migration(test_db: Database) -> None:
    """Test database migrations."""
    cursor = test_db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cursor.fetchall()}

    assert "value_stream_runs" in tables
    assert "stages" in tables
    assert "policy_decisions" in tables
    assert "evaluations" in tables
    assert "costs" in tables


@pytest.mark.unit
def test_save_and_load_run(repository: ValueStreamRepository) -> None:
    """Test save and load run."""
    run = RunRecord(
        id="run-001",
        problem_id="problem-001",
        status="running",
        started_at=datetime.now(UTC),
        total_cost_usd=1.23,
    )

    repository.save_run(run)

    loaded_run = repository.get_run("run-001")

    assert loaded_run is not None
    assert loaded_run.id == "run-001"
    assert loaded_run.problem_id == "problem-001"
    assert loaded_run.status == "running"
    assert loaded_run.total_cost_usd == 1.23


@pytest.mark.unit
def test_update_run(repository: ValueStreamRepository) -> None:
    """Test update run."""
    run = RunRecord(
        id="run-002",
        problem_id="problem-002",
        status="running",
        started_at=datetime.now(UTC),
    )

    repository.save_run(run)

    run.status = "completed"
    run.completed_at = datetime.now(UTC)
    run.total_cost_usd = 5.67
    run.outcome_achieved = True

    repository.update_run(run)

    loaded_run = repository.get_run("run-002")
    assert loaded_run is not None
    assert loaded_run.status == "completed"
    assert loaded_run.completed_at is not None
    assert loaded_run.total_cost_usd == 5.67
    assert loaded_run.outcome_achieved is True


@pytest.mark.unit
def test_list_runs(repository: ValueStreamRepository) -> None:
    """Test list runs."""
    for i in range(5):
        run = RunRecord(
            id=f"run-{i:03d}",
            problem_id=f"problem-{i:03d}",
            status="completed",
            started_at=datetime.now(UTC),
        )
        repository.save_run(run)

    runs = repository.list_runs(limit=10)
    assert len(runs) == 5


@pytest.mark.unit
def test_save_stage(repository: ValueStreamRepository) -> None:
    """Test save stage."""
    run = RunRecord(
        id="run-003",
        problem_id="problem-003",
        status="running",
        started_at=datetime.now(UTC),
    )
    repository.save_run(run)

    stage = StageRecord(
        id="stage-001",
        run_id="run-003",
        stage_name="discovery",
        agent_name="discovery-agent",
        status="completed",
        started_at=datetime.now(UTC),
        cost_usd=0.42,
    )

    repository.save_stage(stage)

    stages = repository.get_stages("run-003")
    assert len(stages) == 1
    assert stages[0].stage_name == "discovery"


@pytest.mark.unit
def test_cost_tracking(repository: ValueStreamRepository) -> None:
    """Test cost tracking."""
    run = RunRecord(
        id="run-004",
        problem_id="problem-004",
        status="running",
        started_at=datetime.now(UTC),
    )
    repository.save_run(run)

    cost1 = CostRecord(
        id="cost-001",
        run_id="run-004",
        stage_id=None,
        timestamp=datetime.now(UTC),
        agent_name="discovery-agent",
        model_name="gpt-4",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.30,
    )

    cost2 = CostRecord(
        id="cost-002",
        run_id="run-004",
        stage_id=None,
        timestamp=datetime.now(UTC),
        agent_name="specification-agent",
        model_name="gpt-4",
        input_tokens=2000,
        output_tokens=1000,
        cost_usd=0.60,
    )

    repository.save_cost(cost1)
    repository.save_cost(cost2)

    breakdown = repository.get_cost_breakdown("run-004")

    assert breakdown["discovery-agent"] == 0.30
    assert breakdown["specification-agent"] == 0.60


@pytest.mark.unit
def test_policy_decisions_and_evaluations_and_foreign_keys(
    repository: ValueStreamRepository,
) -> None:
    """Test saving policy decisions, evaluations, and foreign key enforcement."""
    run = RunRecord(
        id="run-005",
        problem_id="problem-005",
        status="running",
        started_at=datetime.now(UTC),
    )
    repository.save_run(run)

    policy_rec = PolicyDecisionRecord(
        id="pd-001",
        run_id="run-005",
        timestamp=datetime.now(UTC),
        agent_name="discovery-agent",
        action="query",
        resource="knowledge_base",
        decision="allow",
        policy_matched="policy_1",
        rationale="Allowed in sandbox",
    )
    repository.save_policy_decision(policy_rec)
    decisions = repository.get_policy_decisions("run-005")
    assert len(decisions) == 1
    assert decisions[0].decision == "allow"

    eval_rec = EvaluationRecord(
        id="eval-001",
        run_id="run-005",
        stage_id=None,
        artifact_type="specification",
        judge_name="schema-validation",
        judge_type="deterministic",
        score=1.0,
        passed=True,
        rationale="Valid schema",
    )
    repository.save_evaluation(eval_rec)
    evals = repository.get_evaluations("run-005")
    assert len(evals) == 1
    assert evals[0].passed is True

    # Foreign key violation should raise IntegrityError
    bad_stage = StageRecord(
        id="stage-bad",
        run_id="nonexistent-run",
        stage_name="discovery",
        agent_name="discovery-agent",
        status="completed",
        started_at=datetime.now(UTC),
    )
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_stage(bad_stage)

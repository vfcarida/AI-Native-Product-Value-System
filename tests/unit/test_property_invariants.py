"""Property-based & stateful invariant unit tests using Hypothesis (Round 7)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from product_value_system.brownfield.routing_strategy import should_route_to_modern
from product_value_system.contracts import expand_env_vars
from product_value_system.harness.budget import Budget, BudgetExhausted
from product_value_system.harness.resilience import (
    CircuitBreaker,
    CircuitState,
    full_jitter_delay,
)
from product_value_system.orchestrator.dependency_graph import AgentDefinition, DependencyGraph
from product_value_system.persistence.database import Database
from product_value_system.persistence.models import RunRecord
from product_value_system.persistence.repository import ValueStreamRepository

REPO_ROOT = Path(__file__).resolve().parents[2]


class CircuitBreakerStateMachine(RuleBasedStateMachine):
    """Stateful invariant verifier for the 3-state CircuitBreaker."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 0.0
        self.failure_threshold = 3
        self.recovery_timeout = 10.0
        self.breaker = CircuitBreaker(
            name="pbt-breaker",
            failure_threshold=self.failure_threshold,
            recovery_timeout_seconds=self.recovery_timeout,
            half_open_max_calls=1,
            clock=lambda: self.now,
        )

    @rule(delta=st.floats(min_value=0.0, max_value=15.0, allow_nan=False, allow_infinity=False))
    def advance_time(self, delta: float) -> None:
        self.now += delta

    @rule()
    def record_failure(self) -> None:
        self.breaker.record_failure()

    @rule()
    def record_success(self) -> None:
        self.breaker.record_success()

    @rule()
    def probe_allow_request(self) -> None:
        pre_state = self.breaker.state
        allowed = self.breaker.allow_request()
        if pre_state == CircuitState.OPEN:
            assert allowed is False
        elif pre_state == CircuitState.CLOSED:
            assert allowed is True

    @invariant()
    def state_and_counters_always_consistent(self) -> None:
        assert self.breaker.state in {
            CircuitState.CLOSED,
            CircuitState.OPEN,
            CircuitState.HALF_OPEN,
        }
        stats = self.breaker.get_stats()
        assert stats.consecutive_failures >= 0
        assert stats.total_failures >= 0
        assert stats.total_rejected >= 0


TestCircuitBreakerStateful = CircuitBreakerStateMachine.TestCase
TestCircuitBreakerStateful.settings = settings(
    max_examples=40,
    stateful_step_count=25,
    deadline=None,
)


@settings(max_examples=100, deadline=None)
@given(
    routing_key=st.text(min_size=1, max_size=64),
    p1=st.integers(min_value=0, max_value=100),
    p2=st.integers(min_value=0, max_value=100),
)
def test_strangler_routing_is_monotonic_sticky_and_boundary_exact(
    routing_key: str,
    p1: int,
    p2: int,
) -> None:
    """Increasing rollout percentage must never flip an already-migrated entity back to legacy."""
    low, high = (p1, p2) if p1 <= p2 else (p2, p1)
    assert should_route_to_modern(routing_key, 0) is False
    assert should_route_to_modern(routing_key, 100) is True
    # Determinism (stickiness)
    first = should_route_to_modern(routing_key, low)
    assert first == should_route_to_modern(routing_key, low)
    # Monotonicity: once routed to modern at `low`, stays modern at `high`
    if first:
        assert should_route_to_modern(routing_key, high) is True


@settings(max_examples=100, deadline=None)
@given(
    attempt=st.integers(min_value=0, max_value=25),
    base_delay=st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
    cap_factor=st.floats(min_value=1.0, max_value=50.0, allow_nan=False, allow_infinity=False),
)
def test_full_jitter_delay_always_bounded_finite_non_negative(
    attempt: int,
    base_delay: float,
    cap_factor: float,
) -> None:
    max_delay = base_delay * cap_factor
    delay = full_jitter_delay(
        attempt, base_delay_seconds=base_delay, max_delay_seconds=max_delay
    )
    upper = min(max_delay, base_delay * (2**attempt))
    assert not math.isnan(delay)
    assert 0.0 <= delay <= upper + 1e-12


@settings(max_examples=60, deadline=None)
@given(
    n_nodes=st.integers(min_value=1, max_value=10),
    edge_bits=st.lists(st.booleans(), min_size=45, max_size=45),
)
def test_dependency_graph_layers_preserve_topological_and_parallel_invariants(
    n_nodes: int,
    edge_bits: list[bool],
) -> None:
    """Every random DAG must partition into valid strictly-ordered parallel layers."""
    dag = DependencyGraph()
    names = [f"agent_{i}" for i in range(n_nodes)]
    edges: list[tuple[str, str]] = []
    bit_idx = 0

    for i in range(n_nodes):
        deps: list[str] = []
        for j in range(i):
            if edge_bits[bit_idx]:
                deps.append(names[j])
                edges.append((names[j], names[i]))
            bit_idx += 1
        dag.add_agent(AgentDefinition(name=names[i], depends_on=deps))

    dag.validate()
    layers = dag.get_execution_layers()

    # Invariant 1: Every registered agent appears in exactly one layer
    node_to_layer: dict[str, int] = {}
    for layer in layers:
        for agent_name in layer.agents:
            assert agent_name not in node_to_layer
            node_to_layer[agent_name] = layer.layer_number
    assert set(node_to_layer.keys()) == set(names)

    # Invariant 2: Every dependency edge u -> v strictly increases layer number
    for upstream, downstream in edges:
        assert node_to_layer[upstream] < node_to_layer[downstream]

    # Invariant 3: All distinct agents within the same layer can safely run in parallel
    for layer in layers:
        for idx, a in enumerate(layer.agents):
            for b in layer.agents[idx + 1 :]:
                assert dag.can_run_parallel(a, b) is True


@settings(max_examples=60, deadline=None)
@given(
    max_usd=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    charges=st.lists(
        st.floats(min_value=0.01, max_value=5.0, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=25,
    ),
)
def test_budget_hard_enforcement_never_silently_overspends(
    max_usd: float,
    charges: list[float],
) -> None:
    alerts: list[float] = []
    budget = Budget(
        max_usd=max_usd,
        alert_threshold=0.8,
        alert_callback=lambda spent, limit, frac: alerts.append(frac),
    )

    for charge in charges:
        if budget.spent_usd + charge >= max_usd:
            with pytest.raises(BudgetExhausted):
                budget.check_before_spend(estimated_cost_usd=charge)
            break
        budget.check_before_spend(estimated_cost_usd=charge)
        prev = budget.spent_usd
        budget.spend(charge, tokens=10)
        assert budget.spent_usd >= prev

    assert budget.spent_usd < max_usd


@settings(max_examples=60, deadline=None)
@given(
    plain_text=st.text(max_size=64).filter(lambda s: "${" not in s),
    default_val=st.text(
        alphabet=st.characters(blacklist_characters="}"),
        min_size=0,
        max_size=20,
    ),
)
def test_expand_env_vars_passthrough_and_default_invariants(
    plain_text: str,
    default_val: str,
) -> None:
    assert expand_env_vars(plain_text, env={}) == plain_text
    tpl = f"${{UNSET_PBT_VAR:-{default_val}}}"
    assert expand_env_vars(tpl, env={}) == default_val
    assert expand_env_vars(tpl, env={"UNSET_PBT_VAR": "override"}) == "override"


@pytest.mark.parametrize("outcome_val", [True, False, None])
def test_repository_run_roundtrip_edge_cases(
    tmp_path: Path,
    outcome_val: bool | None,
) -> None:
    db = Database(tmp_path / "pvs_pbt.db", migrations_dir=REPO_ROOT / "migrations")
    db.connect()
    db.migrate()
    repo = ValueStreamRepository(db)

    assert repo.get_run("nonexistent-run-id") is None

    now = datetime.now(UTC)
    run = RunRecord(
        id=f"run-{outcome_val}",
        problem_id="prob-edge",
        status="completed" if outcome_val is not None else "running",
        started_at=now,
        completed_at=now if outcome_val is not None else None,
        total_cost_usd=1.2345,
        outcome_achieved=outcome_val,
        artifact_dir=str(tmp_path / "artifacts"),
    )
    repo.save_run(run)

    loaded = repo.get_run(run.id)
    assert loaded is not None
    assert loaded.id == run.id
    assert loaded.outcome_achieved is outcome_val
    assert abs(loaded.total_cost_usd - 1.2345) < 1e-9
    db.close()

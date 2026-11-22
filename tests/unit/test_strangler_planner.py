"""Unit tests for EVO-031 BoundaryAnalyzer, StranglerPlanner, ACLGenerator & RoutingStrategy."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from product_value_system.brownfield import (
    ACLGenerator,
    BoundaryAnalyzer,
    Coupling,
    GitInsights,
    Hotspot,
    Module,
    Ownership,
    RoutingStrategyPlanner,
    RoutingStrategyType,
    StranglerPlanner,
    load_strangler_config,
    should_route_to_modern,
)
from product_value_system.security.hybrid_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_boundary_detection_static_and_size_filtering() -> None:
    code_structure = {
        "src/payments/charge.py": {"lines_of_code": 400, "cyclomatic_complexity": 12},
        "src/payments/refund.py": {"lines_of_code": 300, "cyclomatic_complexity": 8},
        "src/tiny/helper.py": {"lines_of_code": 50, "cyclomatic_complexity": 2},
        "src/monolith/a.py": {"lines_of_code": 600, "cyclomatic_complexity": 10},
        "src/monolith/b.py": {"lines_of_code": 600, "cyclomatic_complexity": 10},
    }
    analyzer = BoundaryAnalyzer(
        {
            "boundary_detection": {
                "method": "static_analysis",
                "min_module_size_lines": 500,
                "max_module_size_lines": 1000,
            }
        }
    )
    modules = analyzer.identify_modules(code_structure)
    names = {m.name for m in modules}

    # tiny (50 LOC < 500) filtered out; monolith (1200 LOC > 1000) split into 2 parts
    assert "tiny" not in names
    assert "payments" in names
    assert {"monolith_part1", "monolith_part2"} <= names

    payments = next(m for m in modules if m.name == "payments")
    assert payments.lines_of_code == 700
    assert payments.avg_cyclomatic_complexity == pytest.approx(10.0)


def test_boundary_detection_dependency_graph_clustering(tmp_path: Path) -> None:
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "billing").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "tokens.py").write_text("def issue():\n    return 't'\n")
    (tmp_path / "src" / "auth" / "login.py").write_text(
        "from auth.tokens import issue\ndef sign_in():\n    return issue()\n"
    )
    (tmp_path / "src" / "billing" / "invoice.py").write_text("def create():\n    return 1\n")

    code_structure = {
        "src/auth/tokens.py": {"lines_of_code": 200, "cyclomatic_complexity": 4},
        "src/auth/login.py": {"lines_of_code": 250, "cyclomatic_complexity": 6},
        "src/billing/invoice.py": {"lines_of_code": 300, "cyclomatic_complexity": 5},
    }
    analyzer = BoundaryAnalyzer(
        {"boundary_detection": {"method": "dependency_graph", "min_module_size_lines": 0}}
    )
    modules = analyzer.identify_modules(code_structure, repo_path=tmp_path)
    by_name = {m.name: m for m in modules}
    assert set(by_name.keys()) == {"auth", "billing"}
    assert sorted(by_name["auth"].file_paths) == ["src/auth/login.py", "src/auth/tokens.py"]
    assert by_name["auth"].coupling_score == 0.0


def test_module_scoring_weights_and_negative_coupling() -> None:
    low_coupling = Module(
        name="clean_hot",
        file_paths=["src/clean/a.py"],
        lines_of_code=800,
        complexity_score=0.8,
        coupling_score=0.1,
        hotspot_score=0.9,
        business_value_score=0.7,
    )
    high_coupling = Module(
        name="tangled_hot",
        file_paths=["src/tangled/a.py"],
        lines_of_code=800,
        complexity_score=0.8,
        coupling_score=0.95,
        hotspot_score=0.9,
        business_value_score=0.7,
    )

    yaml_weights = [
        {"hotspot_score": 0.4},
        {"complexity_score": 0.3},
        {"coupling_score": -0.2},
        {"business_value": 0.1},
    ]
    dict_weights = {
        "hotspot_score": 0.4,
        "complexity_score": 0.3,
        "coupling_score": 0.2,
        "business_value": 0.1,
    }
    # Both YAML negative weight and dict positive weight must prefer LOW coupling identically
    assert low_coupling.compute_priority(yaml_weights) == pytest.approx(
        low_coupling.compute_priority(dict_weights)
    )
    assert low_coupling.compute_priority(yaml_weights) > high_coupling.compute_priority(
        yaml_weights
    )


async def test_priority_ranking_prefers_high_churn_low_coupling() -> None:
    code_structure = {
        "src/payments/core.py": {"lines_of_code": 900, "cyclomatic_complexity": 20},
        "src/tangled/core.py": {"lines_of_code": 900, "cyclomatic_complexity": 10},
        "src/stable/core.py": {"lines_of_code": 900, "cyclomatic_complexity": 4},
    }
    git_insights = GitInsights(
        hotspots=[
            Hotspot("src/payments/core.py", 25, 1200, 4.17, rank=1),
            Hotspot("src/tangled/core.py", 12, 400, 2.0, rank=2),
        ],
        couplings=[
            Coupling("src/tangled/core.py", "src/stable/core.py", 0.9, 9, 10, 10),
        ],
        total_commits=40,
    )

    planner = StranglerPlanner({"boundary_detection": {"min_module_size_lines": 100}})
    plans = await planner.plan(git_insights, code_structure)

    assert len(plans) == 3
    assert plans[0].target_module.name == "payments"
    assert plans[0].priority_score > plans[1].priority_score


async def test_replacement_step_generation_prerequisites_and_effort() -> None:
    code_structure = {"src/orders/service.py": {"lines_of_code": 1200, "cyclomatic_complexity": 12}}
    planner = StranglerPlanner({"boundary_detection": {"min_module_size_lines": 0}})
    plans = await planner.plan(GitInsights(), code_structure)

    assert len(plans) == 1
    plan = plans[0]
    assert len(plan.replacement_steps) == 8
    assert [s.step_number for s in plan.replacement_steps] == list(range(1, 9))
    for step in plan.replacement_steps:
        assert step.risk_level in {"LOW", "MEDIUM", "HIGH"}
        assert all(p < step.step_number for p in step.prerequisites)
    assert plan.total_estimated_effort_hours == sum(
        s.estimated_effort_hours for s in plan.replacement_steps
    )


def test_acl_template_generation_and_python_compilation(tmp_path: Path) -> None:
    mod_file = tmp_path / "src" / "user-auth" / "service.py"
    mod_file.parent.mkdir(parents=True)
    mod_file.write_text(
        "def authenticate(user_id, token):\n"
        "    return True\n\n"
        "def _private_helper():\n"
        "    return False\n\n"
        "def revoke_session(session_id):\n"
        "    return None\n",
        encoding="utf-8",
    )
    module = Module(
        name="user-auth",
        file_paths=["src/user-auth/service.py"],
        lines_of_code=120,
    )
    gen = ACLGenerator()
    code = gen.generate(module, repo_path=tmp_path)

    # Must compile cleanly as valid Python with sanitized class name & extracted public methods
    compile(code, "<user_auth_acl>", "exec")
    assert "class UserAuthACL:" in code
    assert "def authenticate(" in code
    assert "def revoke_session(" in code
    assert "_private_helper" not in code
    # Translation helpers must appear once (not duplicated per method)
    assert code.count("def _translate_to_modern(") == 1
    assert code.count("def _translate_to_legacy(") == 1


def test_routing_strategy_all_three_backends_and_bucketing() -> None:
    for strat in (
        RoutingStrategyType.FEATURE_FLAG,
        RoutingStrategyType.REVERSE_PROXY,
        RoutingStrategyType.API_GATEWAY,
    ):
        planner = RoutingStrategyPlanner(
            {
                "routing": {
                    "strategy": strat.value,
                    "stages": [1, 5, 25, 50, 100],
                    "monitoring_duration_per_stage_hours": 24,
                }
            }
        )
        plan = planner.build_plan("payments")
        assert plan.strategy_type == strat
        assert [s.percentage for s in plan.stages] == [1, 5, 25, 50, 100]
        assert all(s.monitoring_duration_hours == 24 for s in plan.stages)
        assert len(plan.rollback_procedure) >= 2
        md = plan.to_markdown()
        assert "Traffic Routing Strategy for payments" in md
        assert "100%" in md

    # Consistent bucketing invariants
    assert should_route_to_modern("user-1", 0) is False
    assert should_route_to_modern("user-1", 100) is True
    first = should_route_to_modern("user-42", 25)
    assert should_route_to_modern("user-42", 25) == first
    # Monotonic: if routed at 25%, must also be routed at 50%
    routed_25 = sum(should_route_to_modern(f"u-{i}", 25) for i in range(1000))
    routed_50 = sum(should_route_to_modern(f"u-{i}", 50) for i in range(1000))
    assert 180 <= routed_25 <= 320
    assert routed_50 > routed_25


async def test_risk_assessment_coupling_complexity_size_and_silos() -> None:
    # Low-risk module
    planner = StranglerPlanner({"boundary_detection": {"min_module_size_lines": 0}})
    low_plans = await planner.plan(
        GitInsights(),
        {"src/clean/mod.py": {"lines_of_code": 300, "cyclomatic_complexity": 3}},
    )
    assert "LOW risk" in low_plans[0].risk_assessment

    # High-risk module (high coupling, high complexity, >5000 LOC, single-author silo)
    high_git = GitInsights(
        couplings=[Coupling("src/risky/mod.py", "src/other/mod.py", 0.85, 8, 9, 9)],
        ownership={
            "src/risky/mod.py": Ownership(
                file_path="src/risky/mod.py",
                primary_author="author_solo",
                primary_ownership_pct=100.0,
                author_count=1,
                author_distribution={"author_solo": 100.0},
                bus_factor=1,
                is_knowledge_silo=True,
            )
        },
    )
    high_plans = await planner.plan(
        high_git,
        {"src/risky/mod.py": {"lines_of_code": 6000, "cyclomatic_complexity": 30}},
    )
    risk_text = high_plans[0].risk_assessment
    assert "HIGH coupling" in risk_text
    assert "HIGH complexity" in risk_text
    assert ">5K LOC" in risk_text
    assert "knowledge silos" in risk_text


async def test_empty_codebase_handling() -> None:
    planner = StranglerPlanner()
    assert await planner.plan(GitInsights(), {}) == []


async def test_single_module_codebase() -> None:
    planner = StranglerPlanner({"boundary_detection": {"min_module_size_lines": 0}})
    plans = await planner.plan(
        GitInsights(),
        {"src/solo/app.py": {"lines_of_code": 600, "cyclomatic_complexity": 7}},
    )
    assert len(plans) == 1
    assert plans[0].target_module.name == "solo"
    compile(plans[0].acl_template, "<solo_acl>", "exec")


async def test_large_codebase_100_plus_modules() -> None:
    code_structure: dict[str, Any] = {}
    hotspots: list[Hotspot] = []
    for i in range(120):
        fp1 = f"src/domain_{i:03d}/service.py"
        fp2 = f"src/domain_{i:03d}/models.py"
        code_structure[fp1] = {"lines_of_code": 350, "cyclomatic_complexity": 5 + (i % 15)}
        code_structure[fp2] = {"lines_of_code": 250, "cyclomatic_complexity": 3 + (i % 7)}
        if i % 4 == 0:
            hotspots.append(Hotspot(fp1, 10 + (i % 20), 500, (10 + (i % 20)) / 6.0, rank=i + 1))

    planner = StranglerPlanner(
        {"top_candidates": 5, "boundary_detection": {"min_module_size_lines": 500}}
    )
    t0 = time.monotonic()
    plans = await planner.plan(GitInsights(hotspots=hotspots), code_structure)
    elapsed = time.monotonic() - t0

    assert len(plans) == 5
    assert elapsed < 5.0
    # Priority scores must be monotonically non-increasing
    scores = [p.priority_score for p in plans]
    assert scores == sorted(scores, reverse=True)


async def test_strangler_disabled_rollback() -> None:
    planner = StranglerPlanner({"enabled": False})
    plans = await planner.plan(
        GitInsights(),
        {"src/pay/a.py": {"lines_of_code": 1000, "cyclomatic_complexity": 10}},
    )
    assert plans == []


def test_strangler_yaml_config_and_zero_secrets() -> None:
    cfg_path = REPO_ROOT / "config" / "brownfield.yaml"
    cfg = load_strangler_config(cfg_path)
    assert cfg["enabled"] is True
    assert cfg["boundary_detection"]["min_module_size_lines"] == 500
    assert cfg["routing"]["stages"] == [1, 5, 25, 50, 100]
    assert cfg["routing"]["monitoring_duration_per_stage_hours"] == 24

    detector = HybridSecretDetector()
    for rel in ("config/brownfield.yaml", "docs/strangler-fig-modernization.md"):
        findings = detector.detect((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert findings == [], f"Unexpected secret findings in {rel}: {findings}"

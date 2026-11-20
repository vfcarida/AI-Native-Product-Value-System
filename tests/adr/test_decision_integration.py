"""
Integration tests for EVO-018: DecisionAgentADRIntegration, Human Approval Workflow,
Experiment Linkage, and `pvs adr` CLI commands.
"""

import json
from pathlib import Path

import pytest

from product_value_system.adr import ADRManager, DecisionAgentADRIntegration
from product_value_system.cli.main import main as cli_main
from product_value_system.experiment.ab_test import ABTest
from product_value_system.experiment.models import ExperimentDesign, ExperimentHypothesis


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_adr_from_decision_with_async_llm(tmp_path: Path) -> None:
    """DecisionAgentADRIntegration generates a proposed ADR from LLM markdown sections."""

    async def _mock_async_llm(prompt: str, temperature: float = 0.3) -> str:  # noqa: ARG001
        return """## Context

We require a fast, declarative policy authorization engine integrated with AWS.

## Decision

We will adopt AWS Cedar as our authorization engine.

## Alternatives Considered

### 1. Open Policy Agent (OPA)

**Pros**:
- Mature CNCF ecosystem

**Cons**:
- No partial evaluation support

**Verdict**: Rejected due to latency and partial evaluation requirements.

## Consequences

### Positive

- Sub-5ms authorization latency

### Negative

- Team must learn Cedar syntax

## Implementation Plan

Phase 1: Schema definition. Phase 2: Engine integration.

## Validation

- [ ] p95 policy evaluation under 10ms
- [ ] CI policy fallback sync passing
"""

    mgr = ADRManager(adr_dir=tmp_path / "docs" / "adr")
    integration = DecisionAgentADRIntegration(mgr, _mock_async_llm)

    adr_id = await integration.generate_adr_from_decision(
        decision_context={
            "title": "Choose Policy Engine",
            "problem": "Need authorization engine",
            "goal": "Declarative, performant, AWS-integrated",
        },
        alternatives=[
            {"name": "Cedar", "description": "AWS policy engine"},
            {"name": "OPA", "description": "Open Policy Agent"},
        ],
        chosen_alternative="Cedar",
        experiment_results={
            "EXP-001": {
                "winner": "Cedar",
                "confidence": 0.95,
                "metrics": {"latency_ms": 2.3},
            }
        },
        related_requirements=["REQ-SEC-001"],
        related_code=["src/product_value_system/policy/engine.py"],
    )

    assert adr_id == "ADR-0001"
    adr = mgr.get(adr_id)
    assert adr is not None
    assert adr.title == "Choose Policy Engine"
    assert adr.metadata.status == "proposed"  # Human approval required
    assert adr.metadata.authors == ["decision-agent"]
    assert len(adr.alternatives) == 1
    assert adr.alternatives[0]["name"] == "Open Policy Agent (OPA)"
    assert any("EXP-001" in ref for ref in adr.references)
    assert adr.metadata.related_requirements == ["REQ-SEC-001"]

    # Human architect approves draft -> accepted
    approved = integration.approve_adr(adr_id, reviewer="alice")
    assert approved.metadata.status == "accepted"
    assert "alice" in approved.metadata.authors


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_adr_fallback_with_sync_unstructured_llm(tmp_path: Path) -> None:
    """When LLM returns unstructured text or errors, structured fallback still builds full ADR."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    integration = DecisionAgentADRIntegration(
        mgr,
        llm_callable=lambda _prompt, **_kwargs: "Unstructured mock response",
    )

    adr_id = await integration.generate_adr_from_decision(
        decision_context={
            "title": "Choose Observability Stack",
            "problem": "Need metrics and SLO burn-rate alerting",
            "goal": "Open-source stack with Prometheus + Grafana",
        },
        alternatives=[
            {"name": "Prometheus + Grafana", "description": "Open-source CNCF stack"},
            {"name": "Datadog", "description": "Managed commercial SaaS"},
        ],
        chosen_alternative="Prometheus + Grafana",
        experiment_results={"experiment_ids": ["EXP-OBS-01"]},
    )

    adr = mgr.get(adr_id)
    assert adr is not None
    assert adr.metadata.status == "proposed"
    assert "Prometheus + Grafana" in adr.decision
    assert len(adr.alternatives) == 2
    assert any("EXP-OBS-01" in ref for ref in adr.references)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_adr_linked_to_evo016_ab_test_result(tmp_path: Path) -> None:
    """A/B test ExperimentResult from EVO-016 links cleanly into generated ADR references."""
    design = ExperimentDesign(
        id="experiment-prompt-v2",
        hypothesis=ExperimentHypothesis(
            primary_metric="quality_score",
            baseline_value=0.80,
            expected_value=0.92,
            minimum_detectable_effect=0.10,
        ),
    )
    ab_test = ABTest(design)
    ab_test.data["control"] = [0.80, 0.81, 0.79, 0.80, 0.82] * 20
    ab_test.data["treatment"] = [0.92, 0.93, 0.91, 0.92, 0.94] * 20
    exp_res = ab_test.analyze()
    assert exp_res.recommendation == "adopt"

    mgr = ADRManager(adr_dir=tmp_path / "adr")
    integration = DecisionAgentADRIntegration(mgr)
    adr_id = await integration.generate_adr_from_decision(
        decision_context={
            "title": "Adopt Structured Discovery Prompt v2",
            "problem": "Improve hypothesis quality score",
            "goal": "Achieve >= 92% quality score",
        },
        alternatives=[
            {"name": "Prompt v1", "description": "Baseline prompt"},
            {"name": "Prompt v2", "description": "Structured rubric prompt"},
        ],
        chosen_alternative="Prompt v2",
        experiment_results={
            exp_res.experiment_id: {
                "winner": "Prompt v2",
                "confidence": 0.95,
                "metrics": {"lift": round(exp_res.lift, 4), "p_value": round(exp_res.p_value, 6)},
            }
        },
    )

    adr = mgr.get(adr_id)
    assert adr is not None
    assert any("experiment-prompt-v2" in ref for ref in adr.references)


@pytest.mark.integration
def test_cli_adr_full_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLI `pvs adr` new, list, show, search, accept, supersede, graph, log, scan-code."""
    adr_dir = tmp_path / "docs" / "adr"

    # 1. new
    assert (
        cli_main(
            [
                "adr",
                "new",
                "Use Cedar for Policy Engine",
                "--author",
                "alice",
                "--context",
                "Need declarative authorization policy engine.",
                "--decision",
                "Use AWS Cedar.",
                "--tag",
                "security",
                "--dir",
                str(adr_dir),
            ]
        )
        == 0
    )
    assert (
        cli_main(
            [
                "adr",
                "new",
                "Switch Policy Engine to OPA",
                "--author",
                "bob",
                "--dir",
                str(adr_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()

    # 2. list (table & json)
    assert cli_main(["adr", "list", "--dir", str(adr_dir)]) == 0
    assert "ADR-0001" in capsys.readouterr().out

    assert cli_main(["adr", "list", "--format", "json", "--dir", str(adr_dir)]) == 0
    listed_json = json.loads(capsys.readouterr().out)
    assert len(listed_json) == 2

    # 3. show
    assert cli_main(["adr", "show", "ADR-0001", "--dir", str(adr_dir)]) == 0
    assert "Use Cedar for Policy Engine" in capsys.readouterr().out

    # 4. search
    assert cli_main(["adr", "search", "policy engine", "--dir", str(adr_dir)]) == 0
    assert "Found 2 ADR(s)" in capsys.readouterr().out

    # 5. accept & supersede
    assert (
        cli_main(["adr", "accept", "ADR-0001", "--reviewer", "carol", "--dir", str(adr_dir)]) == 0
    )
    assert cli_main(["adr", "supersede", "ADR-0001", "ADR-0002", "--dir", str(adr_dir)]) == 0
    capsys.readouterr()

    # 6. graph
    assert cli_main(["adr", "graph", "--dir", str(adr_dir)]) == 0
    graph_out = capsys.readouterr().out
    assert "ADR_0002 -->|supersedes| ADR_0001" in graph_out

    # 7. decision log
    log_file = tmp_path / "DECISION_LOG.md"
    assert cli_main(["adr", "log", "--output", str(log_file), "--dir", str(adr_dir)]) == 0
    assert log_file.exists()
    assert "Architecture Decision Log" in log_file.read_text(encoding="utf-8")

    # 8. scan-code
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "auth.py").write_text("# See ADR-0001\n", encoding="utf-8")
    capsys.readouterr()
    assert cli_main(["adr", "scan-code", "--code", str(src_dir), "--dir", str(adr_dir)]) == 0
    assert "ADR-0001" in capsys.readouterr().out


@pytest.mark.integration
def test_cli_adr_missing_id_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs adr show` / `supersede` returns exit code 1 cleanly for missing IDs."""
    adr_dir = tmp_path / "adr"
    assert cli_main(["adr", "show", "ADR-9999", "--dir", str(adr_dir)]) == 1
    assert cli_main(["adr", "supersede", "ADR-9999", "ADR-0001", "--dir", str(adr_dir)]) == 1
    capsys.readouterr()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_decision_integration_survives_llm_exception_and_cli_deprecate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """LLM failures still emit valid proposed ADR; CLI `pvs adr deprecate` transitions status."""

    async def _failing_llm(_prompt: str, **_kwargs: object) -> str:
        raise RuntimeError("Model provider timeout")

    adr_dir = tmp_path / "adr"
    mgr = ADRManager(adr_dir=adr_dir)
    integration = DecisionAgentADRIntegration(mgr, _failing_llm)
    adr_id = await integration.generate_adr_from_decision(
        decision_context={"title": "Fallback ADR", "problem": "Latency", "goal": "Fast"},
        alternatives=[{"name": "Cache"}, {"name": "Direct DB"}],
        chosen_alternative="Cache",
        experiment_results={},
    )
    assert adr_id == "ADR-0001"
    assert cli_main(["adr", "deprecate", adr_id, "--dir", str(adr_dir)]) == 0
    capsys.readouterr()
    reloaded = mgr.get(adr_id)
    assert reloaded is not None and reloaded.metadata.status == "deprecated"

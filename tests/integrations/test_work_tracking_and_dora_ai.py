"""
Tests for Bidirectional Work Tracking Sync (Jira, Linear, GitHub Issues — EVO-P3-17)
and DORA AI 7-Capabilities Scorecard + AI Rework/Churn + Grafana Panels (EVO-P3-16).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from prometheus_client import CollectorRegistry, generate_latest

from product_value_system.integrations.work_tracking import (
    BidirectionalWorkSync,
    GitHubIssuesWorkTracker,
    JiraWorkTracker,
    LinearWorkTracker,
    WorkItem,
)
from product_value_system.metrics.devx_tracker import DORA_AI_CAPABILITIES, DevXTracker

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_bidirectional_work_sync_across_jira_linear_and_github() -> None:
    # 1. Jira create + pull remote status transition to "done"
    def _jira_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"key": "SRE-42"})
        if request.method == "GET":
            return httpx.Response(200, json={"fields": {"status": {"name": "Done"}}})
        return httpx.Response(204)

    jira = JiraWorkTracker(
        base_url="https://acme.atlassian.net",
        project_key="SRE",
        email="sre@acme.test",
        api_token="test-jira-token",
        transport=httpx.MockTransport(_jira_handler),
    )
    items = [
        WorkItem(
            local_id="kaizen-01",
            title="Automate PgBouncer pool failover drill",
            description="Follow-up from Q3 GameDay",
            source_type="kaizen",
        )
    ]
    sync = BidirectionalWorkSync(jira)
    first = await sync.sync(items)
    assert first.created == 1
    assert items[0].external_id == "SRE-42"

    second = await sync.sync(items)
    assert second.updated_local == 1
    assert items[0].status == "done"
    await jira.close()

    # 2. Linear GraphQL create + status check
    def _linear_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if "issueCreate" in body["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {
                                "id": "lin-uuid-1",
                                "identifier": "ENG-108",
                                "url": "https://linear.app/acme/issue/ENG-108",
                            },
                        }
                    }
                },
            )
        return httpx.Response(
            200, json={"data": {"issue": {"state": {"type": "started"}}}}
        )

    linear = LinearWorkTracker(
        team_id="team-sre",
        api_key="test-linear-key",
        transport=httpx.MockTransport(_linear_handler),
    )
    lin_items = [
        WorkItem(
            local_id="pm-action-7",
            title="Add circuit breaker on billing webhook",
            description="Postmortem INC-404 action item",
            source_type="postmortem-action",
        )
    ]
    lin_sync = BidirectionalWorkSync(linear)
    assert (await lin_sync.sync(lin_items)).created == 1
    assert lin_items[0].external_id == "ENG-108"
    assert (await lin_sync.sync(lin_items)).updated_local == 1
    assert lin_items[0].status == "in_progress"
    await linear.close()

    # 3. GitHub Issues create + update
    def _gh_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                201,
                json={"number": 311, "html_url": "https://github.com/acme/pvs/issues/311"},
            )
        if request.method == "GET":
            return httpx.Response(200, json={"state": "open"})
        return httpx.Response(200, json={"number": 311})

    gh = GitHubIssuesWorkTracker(
        repo="acme/pvs",
        token="test-gh-issues-token",
        transport=httpx.MockTransport(_gh_handler),
    )
    gh_items = [WorkItem(local_id="kz-9", title="Tune HPA cooldown", description="Kaizen")]
    gh_sync = BidirectionalWorkSync(gh)
    assert (await gh_sync.sync(gh_items)).created == 1
    assert (await gh_sync.sync(gh_items)).updated_remote == 1
    await gh.close()


def test_devx_tracker_dora_ai_capabilities_rework_churn_and_grafana_panels() -> None:
    """DevXTracker tracks AI 14d rework, PR queue wait, first-pass acceptance & 7 DORA AI caps."""
    reg = CollectorRegistry()
    tracker = DevXTracker(
        {"enabled": True, "aggregate_only": True, "anonymize": True},
        registry=reg,
    )

    tracker.record_ai_usage("build-agent", accepted=True, cost_usd=0.02, time_saved_seconds=600)
    tracker.record_code_churn(lines_added=200, lines_reworked_within_14d=20, ai_assisted=True)
    tracker.record_pr_queue_wait(1200.0)
    tracker.record_pr_queue_wait(2400.0)
    tracker.record_pr_first_pass(True, ai_assisted=True)
    tracker.record_pr_first_pass(True, ai_assisted=True)
    tracker.record_pr_first_pass(False, ai_assisted=True)
    tracker.record_deployment(success=True)

    eff = tracker.get_ai_effectiveness()
    assert eff["ai_14d_rework_rate"] == pytest.approx(0.10, abs=1e-4)
    assert eff["first_pass_acceptance_rate"] == pytest.approx(0.6667, abs=1e-3)
    assert eff["median_pr_queue_wait_seconds"] == pytest.approx(1800.0, abs=1e-2)

    scorecard = tracker.evaluate_dora_ai_capabilities()
    assert set(scorecard["capabilities"].keys()) == set(DORA_AI_CAPABILITIES)
    assert 0.0 <= scorecard["overall_score"] <= 1.0
    assert scorecard["maturity_tier"] in {"foundational", "emerging", "advanced", "elite"}

    prom_text = generate_latest(reg).decode("utf-8")
    assert "devx_ai_code_churn_ratio" in prom_text
    assert "devx_pr_queue_wait_seconds" in prom_text
    assert "devx_first_pass_acceptance_ratio" in prom_text
    assert "devx_dora_ai_capability_score" in prom_text

    # Verify Grafana dashboard JSON stays valid with all 10 panels including DORA AI metrics
    dash_path = REPO_ROOT / "grafana" / "dashboards" / "improvement-metrics.json"
    dash = json.loads(dash_path.read_text(encoding="utf-8"))
    assert len(dash["panels"]) == 10
    titles = {p["title"] for p in dash["panels"]}
    assert "AI 14-Day Code Rework & Churn Rate" in titles
    assert "PR Review Queue Wait Time (p50 / p95)" in titles
    assert "DORA AI 7-Capabilities Scorecard & First-Pass Acceptance" in titles

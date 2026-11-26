"""
Tests for GitHub Pre-Human PR Triage + Bidirectional Spec Sync (EVO-P3-14)
and Signed Slack Block Kit HITL Approval Webhooks (EVO-P3-17) wired into
the production ASGI Control Plane.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from starlette.testclient import TestClient

from product_value_system.api.app_factory import ProductionSettings, create_production_app
from product_value_system.api.auth import DEFAULT_ROLE_PERMISSIONS, TenantContext
from product_value_system.integrations.github_pr_triage import (
    GitHubAPIClient,
    PreHumanPRTriageEngine,
    PRFileChange,
    sign_github_payload,
    verify_github_signature,
)
from product_value_system.integrations.slack_hitl import (
    build_approval_block_kit,
    sign_slack_request,
    verify_slack_signature,
)

GITHUB_SECRET = "test-github-webhook-hmac-secret"
SLACK_SECRET = "test-slack-signing-hmac-secret"
DEV_API_KEY = "test-wave3-dev-key"


def test_github_pr_triage_scores_clean_vs_risky_prs_and_generates_spec_sync() -> None:
    engine = PreHumanPRTriageEngine(large_pr_threshold=200)

    # 1. Clean PR with code + tests + spec update
    clean = engine.triage_pr(
        repo="acme/checkout",
        pr_number=101,
        pr_title="Improve retry jitter",
        files=[
            PRFileChange(
                filename="src/checkout/retry.py",
                additions=12,
                deletions=4,
                patch="+def backoff(attempt: int) -> float:\n+    return 0.1 * attempt\n",
            ),
            PRFileChange(filename="tests/test_retry.py", additions=20, patch="+assert True\n"),
            PRFileChange(
                filename="spec_kit/specification.yaml", additions=5, patch="+jitter: true\n"
            ),
        ],
    )
    assert clean.risk_level == "low"
    assert clean.recommended_action == "approve_candidate"
    assert clean.spec_drift_detected is False
    assert clean.spec_sync_proposal is None

    # 2. Risky AI-authored PR with eval(), missing tests, touching policy/, no spec update
    risky = engine.triage_pr(
        repo="acme/checkout",
        pr_number=102,
        pr_title="Dynamic policy evaluator",
        labels=["ai-generated"],
        files=[
            PRFileChange(
                filename="src/policy/eval_helper.py",
                additions=45,
                patch="+def run(expr: str):\n+    return eval(expr)\n",
            )
        ],
    )
    assert risky.ai_authored is True
    assert risky.risk_level == "critical"
    assert risky.recommended_action == "block_merge"
    assert risky.spec_drift_detected is True
    assert risky.spec_sync_proposal is not None
    assert risky.spec_sync_proposal.branch_name == "pvs/spec-sync-pr-102"
    rule_ids = {f.rule_id for f in risky.findings}
    assert {"SEC-EVAL", "ARCH-CRITICAL-PATH", "MAINT-NO-TESTS", "SPEC-DRIFT"} <= rule_ids

    md = risky.render_markdown_review()
    assert "PVS Pre-Human PR Triage" in md
    assert "Bidirectional Spec Sync Proposal" in md


@pytest.mark.asyncio
async def test_github_api_client_posts_review_and_opens_spec_sync_pr() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content.decode("utf-8"))))
        return httpx.Response(201, json={"id": 999, "number": 205})

    engine = PreHumanPRTriageEngine()
    report = engine.triage_pr(
        repo="acme/pvs",
        pr_number=55,
        pr_title="Add feature",
        files=[PRFileChange(filename="src/mod.py", additions=10, patch="+x = 1\n")],
    )
    assert report.spec_sync_proposal is not None

    gh = GitHubAPIClient("test-gh-token", transport=httpx.MockTransport(_handler))
    await gh.post_pr_review("acme/pvs", 55, report)
    await gh.open_spec_sync_pr("acme/pvs", report.spec_sync_proposal)
    await gh.close()

    assert calls[0][0] == "/repos/acme/pvs/pulls/55/reviews"
    assert calls[1][0] == "/repos/acme/pvs/pulls"


def test_signed_github_and_slack_hitl_webhooks_on_production_asgi_app(tmp_path: Path) -> None:
    """Production ASGI app enforces HMAC verification on GitHub PR triage & Slack HITL webhooks."""
    settings = ProductionSettings(
        database_url=f"sqlite:///{tmp_path / 'webhooks.db'}",
        enable_devx=True,
        enable_audit=True,
        enable_tracing=False,
        enable_control_plane=True,
        enable_protocols=False,
        enable_integrations=True,
        github_webhook_secret=GITHUB_SECRET,
        slack_signing_secret=SLACK_SECRET,
        api_keys={
            DEV_API_KEY: TenantContext(
                tenant_id="tenant-enterprise",
                user_id="alice-dev",
                role="developer",
                permissions=DEFAULT_ROLE_PERMISSIONS["developer"],
            )
        },
    )
    app = create_production_app(settings)

    with TestClient(app) as client:
        # 1. GitHub PR Triage Webhook rejects invalid signature (401)
        gh_payload = json.dumps(
            {
                "number": 77,
                "repository": {"full_name": "acme/core"},
                "pull_request": {
                    "number": 77,
                    "title": "Add safe helper",
                    "labels": [{"name": "ai-assisted"}],
                },
                "files": [
                    {
                        "filename": "src/helper.py",
                        "additions": 10,
                        "patch": "+def ok() -> int:\n+    return 1\n",
                    },
                    {"filename": "tests/test_helper.py", "additions": 10, "patch": "+assert 1\n"},
                    {"filename": "spec_kit/spec.yaml", "additions": 2, "patch": "+ok: true\n"},
                ],
            }
        ).encode("utf-8")

        assert (
            client.post(
                "/api/v1/webhooks/github/pr-triage",
                content=gh_payload,
                headers={"X-Hub-Signature-256": "sha256=deadbeef"},
            ).status_code
            == 401
        )

        valid_sig = sign_github_payload(gh_payload, GITHUB_SECRET)
        assert verify_github_signature(gh_payload, valid_sig, GITHUB_SECRET) is True
        gh_ok = client.post(
            "/api/v1/webhooks/github/pr-triage",
            content=gh_payload,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": valid_sig,
            },
        )
        assert gh_ok.status_code == 200
        assert gh_ok.json()["report"]["recommended_action"] == "approve_candidate"

        # 2. Create a durable HITL run via Control Plane API
        run = client.post(
            "/api/v1/runs",
            headers={"X-API-Key": DEV_API_KEY},
            json={
                "problem_id": "PROB-SLACK-1",
                "title": "Approve Production Rollout",
                "require_human_approval": True,
            },
        ).json()
        run_id = run["run_id"]
        assert run["status"] == "waiting_approval"

        # Verify Block Kit builder structure
        record = app.state.run_store
        assert record is not None

        # 3. Slack HITL interaction rejects stale timestamp (replay attack -> 401)
        slack_interaction = {
            "type": "block_actions",
            "user": {"id": "U12345", "username": "maria.sre"},
            "actions": [
                {
                    "action_id": "pvs_approve_run",
                    "value": json.dumps({"run_id": run_id, "tenant_id": "tenant-enterprise"}),
                }
            ],
        }
        form_body = urlencode({"payload": json.dumps(slack_interaction)}).encode("utf-8")
        stale_ts = str(int(time.time()) - 900)
        stale_sig = sign_slack_request(form_body, stale_ts, SLACK_SECRET)
        assert verify_slack_signature(form_body, stale_ts, stale_sig, SLACK_SECRET) is False
        assert (
            client.post(
                "/api/v1/webhooks/slack/interactions",
                content=form_body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Slack-Request-Timestamp": stale_ts,
                    "X-Slack-Signature": stale_sig,
                },
            ).status_code
            == 401
        )

        # 4. Valid signed Slack approval resolves the durable HITL run to completed!
        fresh_ts = str(int(time.time()))
        fresh_sig = sign_slack_request(form_body, fresh_ts, SLACK_SECRET)
        slack_ok = client.post(
            "/api/v1/webhooks/slack/interactions",
            content=form_body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Slack-Request-Timestamp": fresh_ts,
                "X-Slack-Signature": fresh_sig,
            },
        )
        assert slack_ok.status_code == 200
        assert slack_ok.json()["status"] == "completed"
        assert slack_ok.json()["decision"] == "approved"

        # Confirm persisted run status via Control Plane API
        fetched = client.get(
            f"/api/v1/runs/{run_id}",
            headers={"X-API-Key": DEV_API_KEY},
        ).json()
        assert fetched["status"] == "completed"
        assert fetched["approvals"][0]["decided_by"] == "slack:maria.sre"


@pytest.mark.asyncio
async def test_build_approval_block_kit_renders_buttons(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from product_value_system.persistence.run_store import DurableRunRecord

    now = datetime.now(UTC)
    rec = DurableRunRecord(
        run_id="run-bk-1",
        tenant_id="tenant-a",
        problem_id="PROB-7",
        problem_payload={"title": "Enable Canary Rollout"},
        status="waiting_approval",
        current_stage="decision_gate",
        created_by="eng",
        created_at=now,
        updated_at=now,
    )
    msg = build_approval_block_kit(rec)
    assert len(msg["blocks"]) == 4
    action_ids = [el["action_id"] for el in msg["blocks"][3]["elements"]]
    assert action_ids == ["pvs_approve_run", "pvs_reject_run"]

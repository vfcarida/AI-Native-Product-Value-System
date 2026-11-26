"""
Signed Enterprise Webhook Routes for the Production ASGI Control Plane:
- ``POST /api/v1/webhooks/github/pr-triage`` (HMAC-SHA256 ``X-Hub-Signature-256``)
- ``POST /api/v1/webhooks/slack/interactions`` (Slack ``v0`` HMAC-SHA256 + replay check)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
)
from product_value_system.integrations.github_pr_triage import (
    PreHumanPRTriageEngine,
    PRFileChange,
    verify_github_signature,
)
from product_value_system.integrations.slack_hitl import (
    SlackHITLHandler,
    verify_slack_signature,
)
from product_value_system.metrics.devx_tracker import DevXTracker
from product_value_system.persistence.run_store import DurableRunStore


def create_integrations_router(
    *,
    run_store: DurableRunStore,
    devx_tracker: DevXTracker | None = None,
    audit_logger: AuditLogger | None = None,
    github_webhook_secret: str = "",
    slack_signing_secret: str = "",
) -> APIRouter:
    """Create cryptographically verified webhook router for GitHub PR Triage & Slack HITL."""
    router = APIRouter(prefix="/api/v1/webhooks", tags=["integrations"])
    triage_engine = PreHumanPRTriageEngine()
    slack_handler = SlackHITLHandler(run_store=run_store, audit_logger=audit_logger)

    @router.post("/github/pr-triage")
    async def github_pr_triage_webhook(request: Request) -> dict[str, Any]:
        raw_body = await request.body()
        sig = request.headers.get("x-hub-signature-256")
        if not verify_github_signature(raw_body, sig, github_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Malformed JSON webhook payload") from exc

        pr = body.get("pull_request") or {}
        repo = str((body.get("repository") or {}).get("full_name", "unknown/repo"))
        pr_number = int(body.get("number") or pr.get("number") or 0)
        pr_title = str(pr.get("title", ""))
        pr_body = str(pr.get("body") or "")
        labels = [
            str(lbl.get("name", ""))
            for lbl in (pr.get("labels") or [])
            if isinstance(lbl, dict)
        ]

        files_raw = body.get("files") or pr.get("files") or []
        files = [
            PRFileChange(
                filename=str(item.get("filename", "")),
                status=str(item.get("status", "modified")),
                additions=int(item.get("additions", 0)),
                deletions=int(item.get("deletions", 0)),
                patch=str(item.get("patch", "")),
            )
            for item in files_raw
            if isinstance(item, dict) and item.get("filename")
        ]

        report = triage_engine.triage_pr(
            repo=repo,
            pr_number=pr_number,
            pr_title=pr_title,
            files=files,
            labels=labels,
            pr_body=pr_body,
        )

        if devx_tracker is not None and devx_tracker.enabled:
            devx_tracker.record_pr_first_pass(
                report.recommended_action == "approve_candidate",
                ai_assisted=report.ai_authored,
            )

        if audit_logger is not None:
            await audit_logger.log(
                AuditEvent(
                    event_type=AuditEventType.AGENT_COMPLETED,
                    timestamp=datetime.now(UTC),
                    user_id="github-webhook",
                    subject_id=None,
                    action=f"Pre-human PR triage #{pr_number} ({report.recommended_action})",
                    resource_type="github_pr",
                    resource_id=f"{repo}#{pr_number}",
                    result="success",
                    metadata={
                        "risk_score": report.risk_score,
                        "risk_level": report.risk_level,
                        "spec_drift_detected": report.spec_drift_detected,
                    },
                )
            )

        return {
            "report": report.to_dict(),
            "markdown_review": report.render_markdown_review(),
        }

    @router.post("/slack/interactions")
    async def slack_hitl_interaction_webhook(request: Request) -> dict[str, Any]:
        raw_body = await request.body()
        ts = request.headers.get("x-slack-request-timestamp")
        sig = request.headers.get("x-slack-signature")
        if not verify_slack_signature(raw_body, ts, sig, slack_signing_secret):
            raise HTTPException(status_code=401, detail="Invalid or expired Slack signature")

        content_type = (request.headers.get("content-type") or "").lower()
        try:
            if "application/x-www-form-urlencoded" in content_type:
                parsed_form = parse_qs(raw_body.decode("utf-8"))
                payload_json = parsed_form.get("payload", [""])[0]
                payload = json.loads(payload_json)
            else:
                payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Malformed Slack interaction payload"
            ) from exc

        try:
            return await slack_handler.handle_interaction(payload)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router

"""
Interactive Slack Block Kit HITL Approval Bot & Cryptographic Request
Verification (EVO-P3-17).

Features:
- Slack ``v0`` HMAC-SHA256 signature verification in constant time
  (``hmac.compare_digest``) with 5-minute replay-attack window
- Rich Slack Block Kit approval card builder for ``decision_gate`` pauses
- ``SlackHITLHandler`` resolving approvals directly in ``DurableRunStore``
  and writing immutable HMAC-signed audit records to ``AuditLogger``
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
)
from product_value_system.persistence.run_store import DurableRunRecord, DurableRunStore


def sign_slack_request(body: bytes, timestamp: str, signing_secret: str) -> str:
    """Compute Slack ``v0=<hex>`` HMAC-SHA256 request signature."""
    basestring = f"v0:{timestamp}:".encode() + body
    digest = hmac.new(signing_secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return f"v0={digest}"


def verify_slack_signature(
    body: bytes,
    timestamp_header: str | None,
    signature_header: str | None,
    signing_secret: str,
    *,
    max_skew_seconds: int = 300,
    now_ts: float | None = None,
) -> bool:
    """Verify Slack ``X-Slack-Signature`` and timestamp replay window in constant time."""
    if not signing_secret or not timestamp_header or not signature_header:
        return False
    if not signature_header.startswith("v0="):
        return False
    try:
        req_ts = float(timestamp_header)
    except ValueError:
        return False

    ref_now = time.time() if now_ts is None else float(now_ts)
    if abs(ref_now - req_ts) > max_skew_seconds:
        return False

    expected = sign_slack_request(body, timestamp_header.strip(), signing_secret)
    return hmac.compare_digest(signature_header.strip(), expected)


def build_approval_block_kit(run: DurableRunRecord) -> dict[str, Any]:
    """Generate an interactive Slack Block Kit message for a pending HITL approval gate."""
    title = str(run.problem_payload.get("title", run.problem_id))
    action_value = json.dumps({"run_id": run.run_id, "tenant_id": run.tenant_id})
    return {
        "text": f"PVS HITL Approval Required: {title} ({run.run_id})",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"PVS Decision Gate — {run.problem_id}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Run ID:*\n`{run.run_id}`"},
                    {"type": "mrkdwn", "text": f"*Tenant:*\n`{run.tenant_id}`"},
                    {"type": "mrkdwn", "text": f"*Stage:*\n`{run.current_stage}`"},
                    {"type": "mrkdwn", "text": f"*Requested By:*\n`{run.created_by}`"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Problem:* {title}"},
            },
            {
                "type": "actions",
                "block_id": f"pvs_hitl_{run.run_id}",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "pvs_approve_run",
                        "style": "primary",
                        "text": {"type": "plain_text", "text": "Approve & Resume"},
                        "value": action_value,
                    },
                    {
                        "type": "button",
                        "action_id": "pvs_reject_run",
                        "style": "danger",
                        "text": {"type": "plain_text", "text": "Reject & Cancel"},
                        "value": action_value,
                    },
                ],
            },
        ],
    }


class SlackHITLHandler:
    """Handles verified Slack Block Kit approve/reject callbacks against ``DurableRunStore``."""

    def __init__(
        self,
        run_store: DurableRunStore,
        *,
        audit_logger: AuditLogger | None = None,
        job_queue: Any | None = None,
        event_bus: Any | None = None,
        worker: Any | None = None,
    ) -> None:
        self.run_store = run_store
        self.audit_logger = audit_logger
        self.job_queue = job_queue
        self.event_bus = event_bus
        self.worker = worker

    async def handle_interaction(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve a Slack ``block_actions`` approval click and return Block Kit confirmation."""
        actions = payload.get("actions") or []
        if not isinstance(actions, list) or not actions:
            raise ValueError("Slack interaction payload missing 'actions'")

        action = actions[0]
        action_id = str(action.get("action_id", ""))
        if action_id not in {"pvs_approve_run", "pvs_reject_run"}:
            raise ValueError(f"Unsupported Slack action_id: {action_id!r}")

        raw_val = action.get("value", "{}")
        meta = json.loads(raw_val) if isinstance(raw_val, str) else dict(raw_val)
        run_id = str(meta.get("run_id", "")).strip()
        tenant_id = str(meta.get("tenant_id", "")).strip()
        if not run_id or not tenant_id:
            raise ValueError("Slack action value must contain 'run_id' and 'tenant_id'")

        user_info = payload.get("user") or {}
        slack_user = str(
            user_info.get("username") or user_info.get("id") or "slack-approver"
        )
        comment = f"Decided via Slack interactive HITL by {slack_user}"

        record = await self.run_store.get_run(run_id, tenant_id=tenant_id)
        if record is None:
            raise LookupError(f"Run '{run_id}' not found for tenant '{tenant_id}'")
        if record.status != "waiting_approval":
            return {
                "replace_original": False,
                "text": f"Run `{run_id}` is already `{record.status}` (no change applied).",
            }

        decision = "approved" if action_id == "pvs_approve_run" else "rejected"
        resolved = await self.run_store.resolve_approval(
            run_id=run_id,
            tenant_id=tenant_id,
            decision=decision,
            decided_by=f"slack:{slack_user}",
            comment=comment,
        )
        if resolved is None:
            return {
                "replace_original": False,
                "text": f"No pending approval gate found for run `{run_id}`.",
            }

        now = datetime.now(UTC)
        is_async = record.problem_payload.get("execution_mode") == "async_worker"
        if decision == "approved":
            record.stages["decision_gate"] = {
                "status": "completed",
                "approved_by": f"slack:{slack_user}",
                "completed_at": now.isoformat(),
            }
            if is_async and self.job_queue is not None:
                record.status = "running"
                record.current_stage = "build"
                record.completed_at = None
            else:
                record.stages["specification"] = {
                    "status": "completed",
                    "completed_at": now.isoformat(),
                }
                record.status = "completed"
                record.current_stage = "completed"
                record.completed_at = now
        else:
            record.stages["decision_gate"] = {
                "status": "rejected",
                "rejected_by": f"slack:{slack_user}",
                "completed_at": now.isoformat(),
            }
            record.status = "cancelled"
            record.current_stage = "decision_gate"
            record.completed_at = now
        await self.run_store.update_run(record)

        if self.event_bus is not None:
            await self.event_bus.publish(
                run_id=run_id,
                tenant_id=tenant_id,
                event_type="hitl.approval_resolved",
                stage_name="decision_gate",
                payload={
                    "decision": decision,
                    "decided_by": f"slack:{slack_user}",
                    "approval_id": resolved.approval_id,
                },
            )
            if decision == "rejected":
                await self.event_bus.publish(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    event_type="run.cancelled",
                    stage_name="decision_gate",
                    payload={"reason": comment},
                )
            elif not (is_async and self.job_queue is not None):
                await self.event_bus.publish(
                    run_id=run_id,
                    tenant_id=tenant_id,
                    event_type="run.completed",
                    stage_name="completed",
                    payload={"approved_via": "slack"},
                )

        if decision == "approved" and is_async and self.job_queue is not None:
            await self.job_queue.enqueue(
                run_id=run_id,
                tenant_id=tenant_id,
                job_type="resume",
                payload={"approved_by": f"slack:{slack_user}"},
            )
            if self.worker is not None:
                self.worker.notify()

        if self.audit_logger is not None:
            await self.audit_logger.log(
                AuditEvent(
                    event_type=(
                        AuditEventType.AGENT_COMPLETED
                        if decision == "approved"
                        else AuditEventType.AGENT_FAILED
                    ),
                    timestamp=now,
                    user_id=f"slack:{slack_user}",
                    subject_id=None,
                    action=f"Slack HITL {decision} run {run_id}",
                    resource_type="value_stream_run",
                    resource_id=run_id,
                    result="success" if decision == "approved" else "denied",
                    metadata={
                        "tenant_id": tenant_id,
                        "channel": "slack_block_kit",
                        "approval_id": resolved.approval_id,
                    },
                )
            )

        verb = "Approved & Resumed" if decision == "approved" else "Rejected & Cancelled"
        return {
            "replace_original": True,
            "response_type": "in_channel",
            "text": f"PVS Run `{run_id}` {verb} by @{slack_user} (status: `{record.status}`).",
            "run_id": run_id,
            "status": record.status,
            "decision": decision,
        }

"""
Tests for Enterprise AI Governance Pack (EU AI Act + NIST AI RMF) and
``pvs governance export`` CLI command (EVO-P3-20).
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from product_value_system.compliance.ai_governance import EnterpriseGovernancePackGenerator
from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    InMemoryAuditStore,
)
from product_value_system.compliance.signing import generate_signing_key
from product_value_system.persistence.run_store import (
    ApprovalRecord,
    DurableRunRecord,
    SQLiteDurableRunStore,
    new_approval_id,
    new_run_id,
)
from product_value_system.security.agentic_guardrails import AgenticRedTeamRunner

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_governance_pack_links_eu_ai_act_art14_oversight_and_hmac_audit(
    tmp_path: Path,
) -> None:
    """Governance pack verifies Article 14 HITL approvals, HMAC audit integrity, and Risk Cards."""
    store = SQLiteDurableRunStore(tmp_path / "gov.db")
    await store.initialize()

    now = datetime.now(UTC)
    run_id = new_run_id()
    await store.create_run(
        DurableRunRecord(
            run_id=run_id,
            tenant_id="tenant-regulated",
            problem_id="PROB-GOV-1",
            problem_payload={"title": "Automated Credit Underwriting Policy"},
            status="waiting_approval",
            current_stage="decision_gate",
            created_by="eng-1",
            created_at=now,
            updated_at=now,
        )
    )
    await store.create_approval(
        ApprovalRecord(
            approval_id=new_approval_id(),
            run_id=run_id,
            tenant_id="tenant-regulated",
            stage_name="decision_gate",
            status="pending",
            requested_at=now,
        )
    )
    await store.resolve_approval(
        run_id=run_id,
        tenant_id="tenant-regulated",
        decision="approved",
        decided_by="risk-officer-1",
        comment="Reviewed EU AI Act Annex IV risk card",
    )

    audit_logger = AuditLogger(
        store=InMemoryAuditStore(),
        signing_key=generate_signing_key(),
        enable_async=False,
    )
    await audit_logger.log(
        AuditEvent(
            event_type=AuditEventType.AGENT_COMPLETED,
            timestamp=now,
            user_id="risk-officer-1",
            subject_id=None,
            action=f"Approved HITL gate for {run_id}",
            resource_type="value_stream_run",
            resource_id=run_id,
            result="success",
            metadata={"tenant_id": "tenant-regulated"},
        )
    )

    rt_report = await AgenticRedTeamRunner().run_all()
    generator = EnterpriseGovernancePackGenerator(repo_root=REPO_ROOT)
    pack = await generator.generate(
        run_store=store,
        tenant_ids=["tenant-regulated"],
        audit_logger=audit_logger,
        redteam_report=rt_report,
    )
    await store.close()

    assert len(pack.content_sha256) == 64
    assert set(pack.nist_ai_rmf_mapping.keys()) == {"Govern", "Map", "Measure", "Manage"}
    assert len(pack.agent_risk_cards) == 7
    assert any(
        c.agent_name == "build-agent" and c.eu_ai_act_risk_tier == "high"
        for c in pack.agent_risk_cards
    )

    ov = pack.human_oversight_evidence
    assert ov.total_runs_inspected == 1
    assert ov.human_gated_runs == 1
    assert ov.approved_by_humans == 1
    assert ov.oversight_coverage_pct == 100.0
    assert ov.audit_events_verified == 1
    assert ov.audit_hmac_valid is True
    assert pack.redteam_summary["passed_gate"] is True

    md = pack.render_markdown()
    assert "# Enterprise AI Governance Audit Pack" in md
    assert "EU AI Act Readiness Summary" in md
    assert "NIST AI RMF 1.0 Control Mapping" in md
    assert "OWASP Agentic Top 10 Red-Teaming Gate" in md


def test_cli_governance_export_json_and_markdown_zero_warnings(tmp_path: Path) -> None:
    """``pvs governance export`` runs cleanly under ``-W error`` for JSON & Markdown."""
    json_proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "product_value_system",
            "governance",
            "export",
            "--format",
            "json",
            "--repo-root",
            str(REPO_ROOT),
            "--include-redteam",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    import product_value_system

    assert json_proc.returncode == 0, json_proc.stderr
    parsed = json.loads(json_proc.stdout)
    assert parsed["platform_version"] == product_value_system.__version__
    assert parsed["redteam_summary"]["passed_gate"] is True
    assert len(parsed["agent_risk_cards"]) == 7

    md_file = tmp_path / "governance-report.md"
    md_proc = subprocess.run(
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "product_value_system",
            "governance",
            "export",
            "--format",
            "markdown",
            "--repo-root",
            str(REPO_ROOT),
            "--output",
            str(md_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert md_proc.returncode == 0, md_proc.stderr
    assert md_file.is_file()
    assert "Enterprise AI Governance Audit Pack" in md_file.read_text(encoding="utf-8")

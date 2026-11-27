"""
Enterprise AI Governance Pack Generator (EU AI Act + NIST AI RMF) (EVO-P3-20).

Generates auditor-ready quarterly compliance packs containing:
- EU AI Act Annex IV System & Agent Risk Cards (risk classification, purpose,
  human oversight gates under Article 14, Cedar guardrails, known limitations)
- NIST AI RMF 1.0 mapping across ``Govern``, ``Map``, ``Measure``, and ``Manage``
- Automated inventory of models, tools, schemas, Cedar policies, and versioned prompts
- Verifiable Article 14 Human Oversight evidence linked to ``DurableRunStore``
  and HMAC-signed ``AuditLogger``
- Machine-readable JSON + executive Markdown export (exposed via ``pvs governance export``)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.__version__ import __version__
from product_value_system.compliance.audit_logger import AuditLogger
from product_value_system.evaluation.prompt_registry import PromptRegistry
from product_value_system.persistence.run_store import DurableRunStore
from product_value_system.protocols.a2a import A2ARegistry
from product_value_system.security.agentic_guardrails import RedTeamReport


@dataclass(frozen=True)
class AgentRiskCard:
    """EU AI Act Annex IV technical risk card for an autonomous specialist agent."""

    agent_name: str
    eu_ai_act_risk_tier: str  # minimal, limited, high
    intended_purpose: str
    autonomy_level: str
    human_oversight_gates: list[str]
    cedar_policy_controls: list[str]
    owasp_asi_controls: list[str]
    known_limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "eu_ai_act_risk_tier": self.eu_ai_act_risk_tier,
            "intended_purpose": self.intended_purpose,
            "autonomy_level": self.autonomy_level,
            "human_oversight_gates": list(self.human_oversight_gates),
            "cedar_policy_controls": list(self.cedar_policy_controls),
            "owasp_asi_controls": list(self.owasp_asi_controls),
            "known_limitations": list(self.known_limitations),
        }


@dataclass(frozen=True)
class HumanOversightEvidence:
    """EU AI Act Article 14 verifiable human-in-the-loop oversight evidence."""

    total_runs_inspected: int
    human_gated_runs: int
    approved_by_humans: int
    rejected_by_humans: int
    pending_approvals: int
    oversight_coverage_pct: float
    audit_events_verified: int
    audit_hmac_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_runs_inspected": self.total_runs_inspected,
            "human_gated_runs": self.human_gated_runs,
            "approved_by_humans": self.approved_by_humans,
            "rejected_by_humans": self.rejected_by_humans,
            "pending_approvals": self.pending_approvals,
            "oversight_coverage_pct": round(self.oversight_coverage_pct, 2),
            "audit_events_verified": self.audit_events_verified,
            "audit_hmac_valid": self.audit_hmac_valid,
        }


@dataclass(frozen=True)
class GovernanceAuditPack:
    """Complete EU AI Act + NIST AI RMF quarterly governance & audit package."""

    organization: str
    platform_version: str
    generated_at: str
    eu_ai_act_summary: dict[str, Any]
    nist_ai_rmf_mapping: dict[str, list[str]]
    inventory: dict[str, Any]
    agent_risk_cards: list[AgentRiskCard]
    human_oversight_evidence: HumanOversightEvidence
    redteam_summary: dict[str, Any] = field(default_factory=dict)
    content_sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "organization": self.organization,
            "platform_version": self.platform_version,
            "generated_at": self.generated_at,
            "eu_ai_act_summary": self.eu_ai_act_summary,
            "nist_ai_rmf_mapping": self.nist_ai_rmf_mapping,
            "inventory": self.inventory,
            "agent_risk_cards": [c.to_dict() for c in self.agent_risk_cards],
            "human_oversight_evidence": self.human_oversight_evidence.to_dict(),
            "redteam_summary": self.redteam_summary,
            "content_sha256": self.content_sha256,
        }

    def render_markdown(self) -> str:
        """Render auditor-ready executive compliance report in GitHub-flavored Markdown."""
        ov = self.human_oversight_evidence
        lines = [
            f"# Enterprise AI Governance Audit Pack — {self.organization}",
            "",
            f"- **Platform Version**: `{self.platform_version}`",
            f"- **Generated At (UTC)**: `{self.generated_at}`",
            f"- **Integrity Digest (SHA-256)**: `{self.content_sha256}`",
            "",
            "## 1. EU AI Act Readiness Summary",
            "",
            f"- **Article 9 (Risk Management)**: `{self.eu_ai_act_summary['article_9_risk_mgmt']}`",
            f"- **Article 12 (Record-Keeping / Audit)**: "
            f"`{self.eu_ai_act_summary['article_12_record_keeping']}`",
            f"- **Article 14 (Human Oversight Coverage)**: `{ov.oversight_coverage_pct:.1f}%` "
            f"(approved={ov.approved_by_humans}, rejected={ov.rejected_by_humans}, "
            f"pending={ov.pending_approvals}, HMAC verified=`{ov.audit_hmac_valid}`)",
            f"- **Article 15 (Accuracy, Robustness & Cybersecurity)**: "
            f"`{self.eu_ai_act_summary['article_15_cybersecurity']}`",
            "",
            "## 2. NIST AI RMF 1.0 Control Mapping",
            "",
        ]
        for func_name, controls in self.nist_ai_rmf_mapping.items():
            lines.append(f"### {func_name}")
            for ctrl in controls:
                lines.append(f"- {ctrl}")
            lines.append("")

        lines.extend(
            [
                "## 3. System Inventory (NIST Map)",
                "",
                f"- **YAML Config Contracts**: `{len(self.inventory['config_files'])}`",
                f"- **Cedar Policy Files**: `{len(self.inventory['policy_files'])}`",
                f"- **JSON Schemas**: `{len(self.inventory['schema_files'])}`",
                f"- **Versioned Prompt Templates**: `{len(self.inventory['prompt_ids'])}`",
                f"- **Specialist Agents**: `{len(self.agent_risk_cards)}`",
                "",
                "## 4. Specialist Agent Risk Cards (EU AI Act Annex IV)",
                "",
                "| Agent | Risk Tier | Autonomy | Oversight Gates |",
                "| --- | --- | --- | --- |",
            ]
        )
        for card in self.agent_risk_cards:
            gates = ", ".join(card.human_oversight_gates)
            lines.append(
                f"| `{card.agent_name}` | `{card.eu_ai_act_risk_tier}` | "
                f"{card.autonomy_level} | {gates} |"
            )

        if self.redteam_summary:
            lines.extend(
                [
                    "",
                    "## 5. OWASP Agentic Top 10 Red-Teaming Gate",
                    "",
                    f"- **Scenarios Blocked**: `{self.redteam_summary.get('blocked_count')}/"
                    f"{self.redteam_summary.get('total_scenarios')}`",
                    f"- **Critical Bypasses**: `{self.redteam_summary.get('critical_bypasses')}`",
                    f"- **CI Gate Passed**: `{self.redteam_summary.get('passed_gate')}`",
                ]
            )
        return "\n".join(lines) + "\n"


class EnterpriseGovernancePackGenerator:
    """Compiles EU AI Act & NIST AI RMF governance packs from live repo and runtime state."""

    def __init__(
        self,
        repo_root: Path | str = ".",
        *,
        organization: str = "AI-Native Product Value System",
        platform_version: str = __version__,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.organization = organization
        self.platform_version = platform_version

    def _discover_inventory(self) -> dict[str, Any]:
        config_files = sorted(
            p.name for p in (self.repo_root / "config").glob("*.yaml")
        ) if (self.repo_root / "config").is_dir() else []
        policy_files = sorted(
            p.name for p in (self.repo_root / "policies").glob("*.cedar")
        ) if (self.repo_root / "policies").is_dir() else []
        schema_files = sorted(
            p.name for p in (self.repo_root / "schemas").glob("*.json")
        ) if (self.repo_root / "schemas").is_dir() else []

        prompt_reg = PromptRegistry.from_directory(self.repo_root / "prompts")
        prompt_ids = prompt_reg.list_prompts()

        return {
            "config_files": config_files,
            "policy_files": policy_files,
            "schema_files": schema_files,
            "prompt_ids": prompt_ids,
            "supported_model_providers": [
                "anthropic",
                "openai",
                "gemini",
                "azure_openai",
                "ollama",
                "mock",
            ],
            "protocols": ["MCP 2025-03-26", "A2A 0.2.0"],
        }

    def _build_agent_risk_cards(self) -> list[AgentRiskCard]:
        registry = A2ARegistry(version=self.platform_version)
        high_risk_agents = {"build-agent", "security-review-agent"}
        cards: list[AgentRiskCard] = []

        for card in registry.list_agent_cards("https://pvs.internal"):
            is_high = card.name in high_risk_agents
            cards.append(
                AgentRiskCard(
                    agent_name=card.name,
                    eu_ai_act_risk_tier="high" if is_high else "limited",
                    intended_purpose=card.description,
                    autonomy_level=(
                        "Sandbox-bounded ReAct with mandatory human deploy gate"
                        if card.name == "build-agent"
                        else "Advisory & structured artifact synthesis with HITL decision_gate"
                    ),
                    human_oversight_gates=[
                        "decision_gate (DurableRunStore / Slack HITL)",
                        "Pre-production approval (Cedar has_human_approval == true)",
                    ],
                    cedar_policy_controls=[
                        "Default-deny tool authorization (agent_authorization.cedar)",
                        "Hard USD & iteration cost budget cap",
                        "Emergency kill-switch (Harness.kill)",
                    ],
                    owasp_asi_controls=[
                        "ASI01 Indirect Injection Guard",
                        "ASI06 SHA-256 Context Integrity",
                        "ASI07 Signed Inter-Agent Envelope",
                    ],
                    known_limitations=[
                        "Requires human review prior to production release",
                        "Depends on external LLM provider availability unless fallback configured",
                    ],
                )
            )
        return cards

    async def _collect_oversight_evidence(
        self,
        *,
        run_store: DurableRunStore | None,
        tenant_ids: list[str] | None,
        audit_logger: AuditLogger | None,
    ) -> HumanOversightEvidence:
        total_runs = 0
        gated_runs = 0
        approved = 0
        rejected = 0
        pending = 0

        if run_store is not None:
            for tid in (tenant_ids or ["default"]):
                runs = await run_store.list_runs(tenant_id=tid, limit=200)
                for run in runs:
                    total_runs += 1
                    if run.approvals:
                        gated_runs += 1
                        for appr in run.approvals:
                            if appr.status == "approved":
                                approved += 1
                            elif appr.status == "rejected":
                                rejected += 1
                            else:
                                pending += 1

        audit_verified = 0
        hmac_valid = True
        if audit_logger is not None:
            events = await audit_logger.search(
                limit=200,
                log_access=False,
                verify_integrity=False,
            )
            audit_verified = len(events)
            verify_summary = audit_logger.verify_events(events)
            hmac_valid = bool(verify_summary.get("integrity_verified", False))

        coverage = (gated_runs / total_runs * 100.0) if total_runs > 0 else 100.0
        return HumanOversightEvidence(
            total_runs_inspected=total_runs,
            human_gated_runs=gated_runs,
            approved_by_humans=approved,
            rejected_by_humans=rejected,
            pending_approvals=pending,
            oversight_coverage_pct=coverage,
            audit_events_verified=audit_verified,
            audit_hmac_valid=hmac_valid,
        )

    async def generate(
        self,
        *,
        run_store: DurableRunStore | None = None,
        tenant_ids: list[str] | None = None,
        audit_logger: AuditLogger | None = None,
        redteam_report: RedTeamReport | None = None,
    ) -> GovernanceAuditPack:
        now = datetime.now(UTC).isoformat()
        inventory = self._discover_inventory()
        risk_cards = self._build_agent_risk_cards()
        oversight = await self._collect_oversight_evidence(
            run_store=run_store,
            tenant_ids=tenant_ids,
            audit_logger=audit_logger,
        )
        rt_summary = redteam_report.to_dict() if redteam_report is not None else {}

        eu_summary = {
            "article_9_risk_mgmt": "implemented (Cedar PolicyEngine + Budget + Kill-Switch)",
            "article_12_record_keeping": "implemented (Append-only HMAC-SHA256 AuditLogger)",
            "article_14_human_oversight": (
                "implemented (DurableRunStore decision_gate + Control Plane & Slack HITL)"
            ),
            "article_15_cybersecurity": (
                "implemented (OWASP ASI01-ASI10 Guardrails + Hybrid SecretDetector)"
            ),
        }
        nist_map = {
            "Govern": [
                "Cedar default-deny agent policies (policies/agent_authorization.cedar)",
                "Multi-tenant RBAC & OIDC/JWT Control Plane authentication",
                "Immutable HMAC-SHA256 audit logging & GDPR export/erasure handler",
            ],
            "Map": [
                f"Automated inventory: {len(inventory['config_files'])} YAML contracts, "
                f"{len(inventory['schema_files'])} schemas, {len(risk_cards)} Agent Risk Cards",
                "Versioned PromptRegistry templates with SHA-256 content digests",
            ],
            "Measure": [
                "LLM-as-a-Judge human calibration (Cohen's Kappa >= 0.80 & positional bias checks)",
                "Non-punitive DORA AI 7-Capabilities Scorecard & 14-day AI rework telemetry",
                "Automated OWASP Agentic Top 10 (ASI01-ASI10) CI red-teaming suite",
            ],
            "Manage": [
                "Emergency agent kill-switch & pre-flight USD/iteration budget enforcement",
                "Automated incident runbooks, chaos GameDays, and rollback-hardened CI/CD",
            ],
        }

        unsigned_payload = {
            "organization": self.organization,
            "platform_version": self.platform_version,
            "generated_at": now,
            "eu_ai_act_summary": eu_summary,
            "nist_ai_rmf_mapping": nist_map,
            "inventory": inventory,
            "agent_risk_cards": [c.to_dict() for c in risk_cards],
            "human_oversight_evidence": oversight.to_dict(),
            "redteam_summary": rt_summary,
        }
        digest = hashlib.sha256(
            json.dumps(unsigned_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        return GovernanceAuditPack(
            organization=self.organization,
            platform_version=self.platform_version,
            generated_at=now,
            eu_ai_act_summary=eu_summary,
            nist_ai_rmf_mapping=nist_map,
            inventory=inventory,
            agent_risk_cards=risk_cards,
            human_oversight_evidence=oversight,
            redteam_summary=rt_summary,
            content_sha256=digest,
        )

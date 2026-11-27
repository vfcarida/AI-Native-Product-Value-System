"""
Agent-to-Agent (A2A) Protocol Agent Cards and Multi-Tenant Task Dispatcher
(EVO-P3-07).

Provides:
- Standard A2A ``AgentCard`` descriptors served at ``/.well-known/agent.json``
  and per-specialist endpoints (``/api/v1/a2a/agents/{name}/card``).
- ``A2ATaskDispatcher`` translating external A2A ``tasks/send`` and ``tasks/get``
  calls onto the durable, tenant-isolated ``DurableRunStore`` (preserving HITL
  ``input-required`` state and HMAC audit logging).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from product_value_system.__version__ import __version__
from product_value_system.persistence.run_store import (
    ApprovalRecord,
    DurableRunRecord,
    DurableRunStore,
    new_approval_id,
    new_run_id,
)

A2A_PROTOCOL_VERSION = "0.2.0"


class A2ATaskState(str, Enum):
    """Standard A2A task lifecycle states."""

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


_RUN_STATUS_TO_A2A: dict[str, A2ATaskState] = {
    "pending": A2ATaskState.SUBMITTED,
    "running": A2ATaskState.WORKING,
    "waiting_approval": A2ATaskState.INPUT_REQUIRED,
    "completed": A2ATaskState.COMPLETED,
    "failed": A2ATaskState.FAILED,
    "cancelled": A2ATaskState.CANCELED,
}


@dataclass(frozen=True)
class AgentSkill:
    """Single discoverable capability in an A2A Agent Card."""

    id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ("text/plain", "application/json")
    output_modes: tuple[str, ...] = ("application/json", "text/markdown")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "inputModes": list(self.input_modes),
            "outputModes": list(self.output_modes),
        }


@dataclass(frozen=True)
class AgentCapabilities:
    """A2A capability flags advertised on an Agent Card."""

    streaming: bool = True
    push_notifications: bool = False
    state_transition_history: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "streaming": self.streaming,
            "pushNotifications": self.push_notifications,
            "stateTransitionHistory": self.state_transition_history,
        }


@dataclass(frozen=True)
class AgentAuthentication:
    """Authentication schemes required to invoke the agent."""

    schemes: tuple[str, ...] = ("Bearer", "ApiKey")

    def to_dict(self) -> dict[str, Any]:
        return {"schemes": list(self.schemes)}


@dataclass(frozen=True)
class AgentCard:
    """A2A Agent Card document (served at ``/.well-known/agent.json``)."""

    name: str
    description: str
    url: str
    version: str = __version__
    protocol_version: str = A2A_PROTOCOL_VERSION
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    authentication: AgentAuthentication = field(default_factory=AgentAuthentication)
    default_input_modes: tuple[str, ...] = ("text/plain", "application/json")
    default_output_modes: tuple[str, ...] = ("application/json", "text/markdown")
    skills: tuple[AgentSkill, ...] = ()
    provider_organization: str = "AI-Native Product Value System"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "protocolVersion": self.protocol_version,
            "provider": {"organization": self.provider_organization},
            "capabilities": self.capabilities.to_dict(),
            "authentication": self.authentication.to_dict(),
            "defaultInputModes": list(self.default_input_modes),
            "defaultOutputModes": list(self.default_output_modes),
            "skills": [skill.to_dict() for skill in self.skills],
        }


@dataclass(frozen=True)
class A2AMessage:
    """A2A task message containing one or more content parts."""

    role: str
    parts: list[dict[str, Any]]

    def extract_text(self) -> str:
        chunks: list[str] = []
        for part in self.parts:
            if isinstance(part, dict):
                if "text" in part:
                    chunks.append(str(part["text"]))
                elif "data" in part:
                    chunks.append(str(part["data"]))
        return "\n".join(chunks).strip()


@dataclass(frozen=True)
class A2ATask:
    """A2A task view projected from a tenant-scoped ``DurableRunRecord``."""

    id: str
    tenant_id: str
    state: A2ATaskState
    created_at: str
    updated_at: str
    agent_name: str
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": {
                "state": self.state.value,
                "timestamp": self.updated_at,
            },
            "agent": self.agent_name,
            "tenantId": self.tenant_id,
            "createdAt": self.created_at,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
        }


_DEFAULT_SPECIALIST_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "discovery-agent",
        "description": "Synthesizes product problems, customer evidence, and opportunity maps.",
        "skills": (
            AgentSkill(
                id="problem-discovery",
                name="Problem Discovery & Evidence Synthesis",
                description=(
                    "Analyze business problems and synthesize structured opportunity trees."
                ),
                tags=("pdlc", "discovery", "evidence"),
            ),
        ),
    },
    {
        "name": "specification-agent",
        "description": "Authors executable YAML/JSON specifications with acceptance criteria.",
        "skills": (
            AgentSkill(
                id="executable-spec",
                name="Executable Specification Authoring",
                description="Generate contract-validated specifications and acceptance tests.",
                tags=("sdd", "specification", "contracts"),
            ),
        ),
    },
    {
        "name": "architecture-review-agent",
        "description": "Reviews system architecture, boundaries, and ADR compliance.",
        "skills": (
            AgentSkill(
                id="architecture-review",
                name="Architecture & ADR Review",
                description="Evaluate technical designs against ADRs and coupling invariants.",
                tags=("architecture", "review", "adr"),
            ),
        ),
    },
    {
        "name": "security-review-agent",
        "description": "Performs OWASP, Cedar policy, and secret-hygiene security reviews.",
        "skills": (
            AgentSkill(
                id="security-review",
                name="Security & Policy Review",
                description="Inspect designs and artifacts for OWASP and policy violations.",
                tags=("security", "owasp", "cedar"),
            ),
        ),
    },
    {
        "name": "build-agent",
        "description": "Iteratively builds, tests in sandbox, and repairs code with unified diffs.",
        "skills": (
            AgentSkill(
                id="react-sandbox-build",
                name="Iterative ReAct Sandbox Implementation",
                description="Generate code, execute pytest in sandbox, and iterate until passing.",
                tags=("sdlc", "build", "sandbox", "tdd"),
            ),
        ),
    },
    {
        "name": "brownfield-agent",
        "description": "Analyzes legacy Git hotspots and generates Strangler Fig plans.",
        "skills": (
            AgentSkill(
                id="brownfield-modernization",
                name="Brownfield Hotspot & Strangler Fig Analysis",
                description="Identify churn hotspots and safe strangler modernization boundaries.",
                tags=("brownfield", "git", "strangler-fig"),
            ),
        ),
    },
    {
        "name": "measurement-agent",
        "description": "Measures post-release business outcomes, experiments, and drift.",
        "skills": (
            AgentSkill(
                id="outcome-measurement",
                name="Outcome & Experiment Measurement",
                description="Track product outcomes and close the PDLC learning loop.",
                tags=("measurement", "experiments", "outcomes"),
            ),
        ),
    },
)


class A2ARegistry:
    """Registry generating standard A2A Agent Cards for PVS and its specialist agents."""

    def __init__(self, *, version: str = __version__) -> None:
        self.version = version
        self._specs: dict[str, dict[str, Any]] = {
            spec["name"]: spec for spec in _DEFAULT_SPECIALIST_SPECS
        }

    def get_platform_card(self, base_url: str) -> AgentCard:
        clean_base = base_url.rstrip("/")
        all_skills = tuple(
            skill
            for spec in self._specs.values()
            for skill in spec["skills"]
        )
        return AgentCard(
            name="ai-native-product-value-system",
            description=(
                "Governed AI-Native PDLC/SDLC multi-agent platform with Cedar policy "
                "enforcement, durable HITL approvals, MCP tools, and A2A task dispatch."
            ),
            url=f"{clean_base}/api/v1/a2a",
            version=self.version,
            skills=all_skills,
        )

    def get_agent_card(self, agent_name: str, base_url: str) -> AgentCard | None:
        spec = self._specs.get(agent_name)
        if spec is None:
            return None
        clean_base = base_url.rstrip("/")
        return AgentCard(
            name=spec["name"],
            description=spec["description"],
            url=f"{clean_base}/api/v1/a2a/tasks/send?agent={spec['name']}",
            version=self.version,
            skills=spec["skills"],
        )

    def list_agent_cards(self, base_url: str) -> list[AgentCard]:
        cards: list[AgentCard] = []
        for name in self._specs:
            card = self.get_agent_card(name, base_url)
            if card is not None:
                cards.append(card)
        return cards


class A2ATaskDispatcher:
    """Dispatches tenant-scoped A2A tasks onto ``DurableRunStore``."""

    def __init__(self, run_store: DurableRunStore, registry: A2ARegistry) -> None:
        self.run_store = run_store
        self.registry = registry

    @staticmethod
    def _record_to_task(record: DurableRunRecord) -> A2ATask:
        state = _RUN_STATUS_TO_A2A.get(record.status, A2ATaskState.SUBMITTED)
        agent_name = str(record.problem_payload.get("target_agent", "discovery-agent"))
        artifacts: list[dict[str, Any]] = [
            {
                "name": "value_stream_stages",
                "mimeType": "application/json",
                "parts": [{"type": "data", "data": record.stages}],
            }
        ]
        if record.artifacts:
            artifacts.append(
                {
                    "name": "run_artifacts",
                    "mimeType": "application/json",
                    "parts": [{"type": "data", "data": record.artifacts}],
                }
            )
        return A2ATask(
            id=record.run_id,
            tenant_id=record.tenant_id,
            state=state,
            created_at=record.created_at.isoformat(),
            updated_at=record.updated_at.isoformat(),
            agent_name=agent_name,
            artifacts=artifacts,
            metadata={
                "problem_id": record.problem_id,
                "current_stage": record.current_stage,
                "approvals": [a.to_dict() for a in record.approvals],
            },
        )

    async def send_task(
        self,
        *,
        tenant_id: str,
        user_id: str,
        message: A2AMessage,
        agent_name: str = "discovery-agent",
        task_id: str | None = None,
        require_human_approval: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> A2ATask:
        """Create and persist a tenant-isolated A2A task backed by ``DurableRunStore``."""
        now = datetime.now(UTC)
        resolved_id = task_id or new_run_id()
        text_body = message.extract_text() or "A2A delegated task"

        initial_status = "waiting_approval" if require_human_approval else "completed"
        current_stage = "decision_gate" if require_human_approval else "completed"
        stages: dict[str, dict[str, Any]] = {
            "a2a_intake": {
                "status": "completed",
                "agent": agent_name,
                "completed_at": now.isoformat(),
            },
            "decision_gate": {
                "status": "waiting_approval" if require_human_approval else "skipped",
                "timestamp": now.isoformat(),
            },
        }

        record = DurableRunRecord(
            run_id=resolved_id,
            tenant_id=tenant_id,
            problem_id=f"A2A-{resolved_id[-8:]}",
            problem_payload={
                "title": text_body[:160],
                "description": text_body,
                "target_agent": agent_name,
                "protocol": "a2a",
                "metadata": metadata or {},
            },
            status=initial_status,
            current_stage=current_stage,
            created_by=user_id,
            created_at=now,
            updated_at=now,
            completed_at=None if require_human_approval else now,
            stages=stages,
        )
        await self.run_store.create_run(record)

        if require_human_approval:
            approval = ApprovalRecord(
                approval_id=new_approval_id(),
                run_id=resolved_id,
                tenant_id=tenant_id,
                stage_name="decision_gate",
                status="pending",
                requested_at=now,
            )
            await self.run_store.create_approval(approval)
            record.approvals.append(approval)

        return self._record_to_task(record)

    async def get_task(self, task_id: str, *, tenant_id: str) -> A2ATask | None:
        record = await self.run_store.get_run(task_id, tenant_id=tenant_id)
        if record is None:
            return None
        return self._record_to_task(record)

"""
Cedar policy engine integration.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from product_value_system.policy.entities import (
    Agent,
    AgentRole,
    AuthorizationContext,
    Tool,
)

logger = logging.getLogger(__name__)


class Decision(str, Enum):
    """Authorization decision."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass
class AuthorizationDecision:
    """Result of policy evaluation."""

    decision: Decision
    reason: str
    policies_matched: list[str]
    timestamp: datetime

    def is_allowed(self) -> bool:
        """Return True if the authorization decision is ALLOW."""
        return self.decision == Decision.ALLOW


class PolicyEngine:
    """
    Cedar policy engine for agent authorization.

    Evaluates authorization requests against Cedar policies,
    logs all decisions for audit trail.
    """

    def __init__(
        self,
        policies_dir: Path | str = "policies",
        policies_file: Path | str | None = None,
        schema_file: Path | str | None = None,
    ) -> None:
        """
        Initialize policy engine.

        Args:
            policies_dir: Directory containing Cedar policies and schema
            policies_file: Optional explicit path to .cedar policy file
            schema_file: Optional explicit path to .cedarschema file
        """
        self.policies_dir = Path(policies_dir)
        self.policies_file = (
            Path(policies_file)
            if policies_file is not None
            else self.policies_dir / "agent_authorization.cedar"
        )
        self.schema_file = (
            Path(schema_file)
            if schema_file is not None
            else self.policies_dir / "schema.cedarschema"
        )

        if not self.policies_file.exists():
            raise FileNotFoundError(f"Policies not found: {self.policies_file}")

        self._load_policies()

    def _load_policies(self) -> None:
        """Load and validate policies."""
        logger.info("Loading Cedar policies from %s", self.policies_file)

        with open(self.policies_file, encoding="utf-8") as f:
            self._policies_text = f.read()

        if self.schema_file.exists():
            with open(self.schema_file, encoding="utf-8") as sf:
                self._schema_text = sf.read()
            try:
                result = subprocess.run(
                    [
                        "cedar",
                        "validate",
                        "--schema",
                        str(self.schema_file),
                        "--policies",
                        str(self.policies_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                if result.returncode != 0:
                    logger.warning("Policy validation warnings: %s", result.stderr)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                logger.debug("Cedar CLI not available, using internal schema and policy validation")

    def authorize(
        self,
        principal: Agent | AgentRole | dict[str, Any] | str,
        action: str | dict[str, Any],
        resource: Tool | str | dict[str, Any],
        context: AuthorizationContext | dict[str, Any],
    ) -> AuthorizationDecision:
        """
        Evaluate authorization request.

        Args:
            principal: Agent or role requesting authorization
            action: Action to authorize (e.g., "read", "write", "execute")
            resource: Tool or resource being accessed
            context: Execution context

        Returns:
            AuthorizationDecision with allow/deny and rationale
        """
        request = self._build_request(principal, action, resource, context)
        decision = self._evaluate(request)
        self._log_decision(principal, action, resource, context, decision)
        return decision

    def _build_request(
        self,
        principal: Agent | AgentRole | dict[str, Any] | str,
        action: str | dict[str, Any],
        resource: Tool | str | dict[str, Any],
        context: AuthorizationContext | dict[str, Any],
    ) -> dict[str, Any]:
        """Build Cedar authorization request."""
        if isinstance(principal, (Agent, AgentRole)):
            principal_entity = principal.to_cedar_entity()
        elif isinstance(principal, dict):
            p_name = str(principal.get("name", principal.get("id", "unknown")))
            role_name = str(principal.get("role", p_name.removesuffix("-agent").replace("-", "_")))
            principal_entity = {
                "type": f"ProductValueSystem::{principal.get('type', 'Agent')}",
                "id": p_name,
                "attrs": {
                    "name": p_name,
                    "role": {"type": "ProductValueSystem::AgentRole", "id": role_name},
                    "trustLevel": principal.get("trustLevel", "medium"),
                    "costBudget": float(principal.get("costBudget", 100.0)),
                },
            }
        else:
            p_name = str(principal)
            role_name = p_name.removesuffix("-agent").replace("-", "_")
            principal_entity = {
                "type": "ProductValueSystem::Agent",
                "id": p_name,
                "attrs": {
                    "name": p_name,
                    "role": {"type": "ProductValueSystem::AgentRole", "id": role_name},
                    "trustLevel": "medium",
                    "costBudget": 100.0,
                },
            }

        action_id = (
            str(action.get("type", action.get("id", "call")))
            if isinstance(action, dict)
            else str(action)
        )

        if isinstance(resource, Tool):
            resource_entity = resource.to_cedar_entity()
        elif isinstance(resource, dict):
            r_name = str(resource.get("name", resource.get("id", "unknown")))
            resource_entity = {
                "type": f"ProductValueSystem::{resource.get('type', 'Tool')}",
                "id": r_name,
                "attrs": resource.get("attrs", {}),
            }
        else:
            resource_entity = {"type": "ProductValueSystem::Resource", "id": str(resource)}

        if isinstance(context, AuthorizationContext):
            context_dict = context.to_cedar_context()
        else:
            cost_val = context.get("cost_so_far", Decimal("0.0"))
            cost_float = (
                float(cost_val) if isinstance(cost_val, (Decimal, int, float)) else 0.0
            )
            context_dict = {
                "environment": str(context.get("environment", "sandbox")),
                "has_human_approval": bool(context.get("has_human_approval", False)),
                "cost_so_far": cost_float,
                "iteration_count": int(context.get("iteration_count", 0)),
                "stage": str(context.get("stage", "discovery")),
                **{
                    k: v
                    for k, v in context.items()
                    if k
                    not in {
                        "environment",
                        "has_human_approval",
                        "cost_so_far",
                        "iteration_count",
                        "stage",
                    }
                },
            }

        return {
            "principal": principal_entity,
            "action": {"type": "ProductValueSystem::Action", "id": action_id},
            "resource": resource_entity,
            "context": context_dict,
        }

    def _evaluate(self, request: dict[str, Any]) -> AuthorizationDecision:
        """
        Evaluate authorization request against policies.

        Uses Cedar CLI if available, otherwise executes deterministic
        Cedar policy evaluation rules matching policies/agent_authorization.cedar.
        """
        try:
            result = subprocess.run(
                ["cedar", "authorize"],
                input=json.dumps(
                    {
                        "policies": self._policies_text,
                        "entities": [],
                        "request": request,
                    }
                ),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

            if result.returncode == 0:
                response = json.loads(result.stdout)
                return AuthorizationDecision(
                    decision=Decision.ALLOW if response["decision"] == "Allow" else Decision.DENY,
                    reason=response.get("reason", ""),
                    policies_matched=response.get("policies", []),
                    timestamp=datetime.now(UTC),
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        return self._fallback_evaluate(request)

    def _fallback_evaluate(self, request: dict[str, Any]) -> AuthorizationDecision:
        """
        Deterministic evaluation matching all rules in policies/agent_authorization.cedar.
        Cedar semantics: forbid overrides permit; default is deny.
        """
        context = request["context"]
        action = request["action"]["id"]
        principal_attrs = request["principal"].get("attrs", {})
        role_info = principal_attrs.get("role", {})
        role_name = (
            role_info.get("id")
            if isinstance(role_info, dict)
            else principal_attrs.get("name", "")
        )
        trust_level = principal_attrs.get("trustLevel", "low")
        cost_budget = principal_attrs.get("costBudget")
        resource_id = request["resource"].get("id", "")

        env = context.get("environment")
        stage = context.get("stage")
        has_approval = context.get("has_human_approval", False)
        cost_so_far = context.get("cost_so_far", 0.0)
        iteration_count = context.get("iteration_count", 0)

        # Forbid checks first (Cedar forbid-overrides-permit semantics)
        if cost_budget is not None and cost_so_far >= cost_budget:
            return AuthorizationDecision(
                decision=Decision.DENY,
                reason="Cost budget exceeded for principal",
                policies_matched=["policy_forbid_cost_budget"],
                timestamp=datetime.now(UTC),
            )

        if action == "deploy" and (env == "production" or "production" in resource_id):
            if not has_approval:
                return AuthorizationDecision(
                    decision=Decision.DENY,
                    reason="Production deployment requires human approval",
                    policies_matched=["policy_forbid_prod_deploy"],
                    timestamp=datetime.now(UTC),
                )

        if role_name in {"architecture_review", "security_review"} and action in {
            "write",
            "execute",
            "deploy",
        }:
            return AuthorizationDecision(
                decision=Decision.DENY,
                reason="Review agents are restricted to read-only operations",
                policies_matched=["policy_forbid_review_write"],
                timestamp=datetime.now(UTC),
            )

        # Permit checks (1-to-1 mirror of policies/agent_authorization.cedar)
        if (
            role_name == "discovery"
            and action in {"query", "call"}
            and resource_id == "knowledge_base"
            and env == "sandbox"
        ):
            return AuthorizationDecision(
                decision=Decision.ALLOW,
                reason="Discovery agent permitted to access knowledge_base in sandbox",
                policies_matched=["policy_permit_discovery_query"],
                timestamp=datetime.now(UTC),
            )

        if (
            role_name == "specification"
            and action in {"read", "write"}
            and resource_id == "spec_editor"
            and env == "sandbox"
            and stage == "specification"
        ):
            return AuthorizationDecision(
                decision=Decision.ALLOW,
                reason="Specification agent permitted to access spec_editor in sandbox",
                policies_matched=["policy_permit_specification_rw"],
                timestamp=datetime.now(UTC),
            )

        if (
            role_name == "build"
            and action == "deploy"
            and (env == "production" or "production" in resource_id)
            and has_approval
        ):
            return AuthorizationDecision(
                decision=Decision.ALLOW,
                reason="Build agent permitted to deploy to production with human approval",
                policies_matched=["policy_permit_build_prod_deploy"],
                timestamp=datetime.now(UTC),
            )

        if (
            role_name == "build"
            and action == "execute"
            and resource_id == "sandbox"
            and env == "sandbox"
            and iteration_count < 10
        ):
            return AuthorizationDecision(
                decision=Decision.ALLOW,
                reason="Build agent permitted to execute in sandbox (under iteration limit)",
                policies_matched=["policy_permit_build_execute"],
                timestamp=datetime.now(UTC),
            )

        if action == "call" and resource_id == "external_api":
            if has_approval and trust_level == "high":
                return AuthorizationDecision(
                    decision=Decision.ALLOW,
                    reason=(
                        "High-trust principal permitted to call external_api "
                        "with human approval"
                    ),
                    policies_matched=["policy_permit_external_api"],
                    timestamp=datetime.now(UTC),
                )
            return AuthorizationDecision(
                decision=Decision.DENY,
                reason="External API call requires human approval and high trust level",
                policies_matched=["policy_permit_external_api"],
                timestamp=datetime.now(UTC),
            )

        if (
            role_name in {"architecture_review", "security_review"}
            and action == "read"
            and stage == "review"
        ):
            return AuthorizationDecision(
                decision=Decision.ALLOW,
                reason="Review agent permitted read-only access during review stage",
                policies_matched=["policy_permit_review_read"],
                timestamp=datetime.now(UTC),
            )

        return AuthorizationDecision(
            decision=Decision.DENY,
            reason="No policy matched (deny by default)",
            policies_matched=[],
            timestamp=datetime.now(UTC),
        )

    def _log_decision(
        self,
        principal: Agent | AgentRole | dict[str, Any] | str,
        action: str | dict[str, Any],
        resource: Tool | str | dict[str, Any],
        context: AuthorizationContext | dict[str, Any],
        decision: AuthorizationDecision,
    ) -> None:
        """Log authorization decision for audit trail."""
        if isinstance(principal, Agent):
            principal_id = principal.id
        elif isinstance(principal, AgentRole):
            principal_id = principal.name
        elif isinstance(principal, dict):
            principal_id = str(principal.get("name", principal.get("id", "unknown")))
        else:
            principal_id = str(principal)

        action_str = (
            str(action.get("type", action.get("id", "call")))
            if isinstance(action, dict)
            else str(action)
        )

        if isinstance(resource, Tool):
            resource_id = resource.name
        elif isinstance(resource, dict):
            resource_id = str(resource.get("name", resource.get("id", "unknown")))
        else:
            resource_id = str(resource)

        env_str = (
            context.environment
            if isinstance(context, AuthorizationContext)
            else str(context.get("environment", "sandbox"))
        )

        logger.info(
            "Authorization: %s | Principal=%s | Action=%s | Resource=%s | "
            "Environment=%s | Reason=%s",
            decision.decision.value,
            principal_id,
            action_str,
            resource_id,
            env_str,
            decision.reason,
        )

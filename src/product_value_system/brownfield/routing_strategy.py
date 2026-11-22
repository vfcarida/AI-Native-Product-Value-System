"""
Progressive traffic routing strategies for Strangler Fig modernization (EVO-031).

Supports feature-flag routing, reverse-proxy weighted routing (NGINX/Envoy),
and API-gateway canary routing, with deterministic consistent hashing for
progressive cutovers (1% -> 5% -> 25% -> 50% -> 100%) and instant 0% rollback.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_NON_SNAKE_RE = re.compile(r"[^a-z0-9_]+")


class RoutingStrategyType(str, Enum):
    """Supported Strangler Fig traffic routing mechanisms."""

    FEATURE_FLAG = "feature_flag"
    REVERSE_PROXY = "reverse_proxy"
    API_GATEWAY = "api_gateway"


@dataclass
class RolloutStage:
    """Single progressive traffic cutover stage."""

    stage_number: int
    percentage: int
    audience: str
    monitoring_duration_hours: int
    success_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_number": self.stage_number,
            "percentage": self.percentage,
            "audience": self.audience,
            "monitoring_duration_hours": self.monitoring_duration_hours,
            "success_criteria": list(self.success_criteria),
        }


@dataclass
class TrafficRoutingPlan:
    """Structured Strangler Fig traffic routing & rollback specification."""

    module_name: str
    strategy_type: RoutingStrategyType
    flag_name: str
    stages: list[RolloutStage]
    monitoring_metrics: list[str]
    rollback_procedure: list[str]
    code_snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_name": self.module_name,
            "strategy_type": self.strategy_type.value,
            "flag_name": self.flag_name,
            "stages": [s.to_dict() for s in self.stages],
            "monitoring_metrics": list(self.monitoring_metrics),
            "rollback_procedure": list(self.rollback_procedure),
            "code_snippet": self.code_snippet,
        }

    def to_markdown(self) -> str:
        stage_lines = "\n".join(
            f"- **Stage {s.stage_number}**: {s.percentage}% traffic ({s.audience}) — "
            f"monitor {s.monitoring_duration_hours}h"
            for s in self.stages
        )
        metric_lines = "\n".join(f"- {m}" for m in self.monitoring_metrics)
        rollback_lines = "\n".join(f"- {r}" for r in self.rollback_procedure)
        return (
            f"# Traffic Routing Strategy for {self.module_name}\n\n"
            f"## Approach: {self.strategy_type.value}\n\n"
            f"- **Routing Key / Flag**: `{self.flag_name}`\n\n"
            f"## Rollout Stages\n\n{stage_lines}\n\n"
            f"## Implementation Snippet\n\n{self.code_snippet}\n\n"
            f"## Monitoring Metrics Per Stage\n\n{metric_lines}\n\n"
            f"## Instant Rollback\n\n{rollback_lines}\n"
        )


def should_route_to_modern(
    routing_key: str,
    rollout_percentage: int,
    *,
    salt: str = "strangler",
) -> bool:
    """Deterministic SHA-256 bucketing in ``[0, 100)`` for consistent canary routing."""
    pct = max(0, min(100, int(rollout_percentage)))
    if pct == 0:
        return False
    if pct == 100:
        return True
    digest = hashlib.sha256(f"{salt}:{routing_key}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < pct


class RoutingStrategyPlanner:
    """Build progressive traffic routing and rollback plans for Strangler Fig cutovers."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = dict(config or {})
        if "strangler_fig" in raw and isinstance(raw["strangler_fig"], dict):
            raw = dict(raw["strangler_fig"])
        rc = raw.get("routing", raw)
        if not isinstance(rc, dict):
            rc = {}

        raw_strat = str(rc.get("strategy", "feature_flag")).strip().lower()
        self.strategy_type = RoutingStrategyType(raw_strat)
        self.stages_pct: list[int] = [
            int(p) for p in rc.get("stages", [1, 5, 25, 50, 100])
        ]
        self.monitoring_duration_hours: int = max(
            1, int(rc.get("monitoring_duration_per_stage_hours", 24))
        )

    @staticmethod
    def _audience_for_percentage(pct: int) -> str:
        if pct <= 1:
            return "internal canary users"
        if pct <= 10:
            return "beta cohort"
        if pct < 50:
            return "early general traffic"
        if pct < 100:
            return "half of production traffic"
        return "full production cutover"

    def build_plan(
        self,
        module_name: str,
        *,
        strategy_override: str | RoutingStrategyType | None = None,
    ) -> TrafficRoutingPlan:
        """Create a progressive ``TrafficRoutingPlan`` for ``module_name``."""
        strat = (
            strategy_override
            if isinstance(strategy_override, RoutingStrategyType)
            else RoutingStrategyType(strategy_override.lower())
            if isinstance(strategy_override, str)
            else self.strategy_type
        )
        snake = _NON_SNAKE_RE.sub("_", module_name.lower()).strip("_") or "module"
        flag_name = f"use_modern_{snake}"

        stages = [
            RolloutStage(
                stage_number=idx + 1,
                percentage=pct,
                audience=self._audience_for_percentage(pct),
                monitoring_duration_hours=self.monitoring_duration_hours,
                success_criteria=[
                    "Error rate <= legacy baseline",
                    "p95 latency within +10% of legacy baseline",
                    "Zero data-consistency discrepancies in shadow/audit checks",
                ],
            )
            for idx, pct in enumerate(self.stages_pct)
        ]

        if strat == RoutingStrategyType.REVERSE_PROXY:
            snippet = (
                "```nginx\n"
                f"split_clients \"${{remote_addr}}${{http_x_user_id}}\" ${snake}_backend {{\n"
                f"    1%    modern_{snake};\n"
                f"    *     legacy_{snake};\n"
                "}\n"
                "```"
            )
            rollback = [
                f"Instant: set split_clients weight for `modern_{snake}` to `0%` and reload proxy",
                "Full: remove upstream entry and route 100% to legacy pool",
            ]
        elif strat == RoutingStrategyType.API_GATEWAY:
            snippet = (
                "```yaml\n"
                f"route: /api/{snake}/*\n"
                "canary:\n"
                f"  modern_service: {snake}-modern-svc\n"
                f"  legacy_service: {snake}-legacy-svc\n"
                "  weight_modern_pct: 1\n"
                "```"
            )
            rollback = [
                "Instant: set API Gateway `weight_modern_pct: 0` via control plane",
                "Full: detach canary route target and keep legacy backend active",
            ]
        else:
            snippet = (
                "```python\n"
                f"if feature_flag.is_enabled('{flag_name}', user_id=user_id):\n"
                f"    return modern_{snake}.handle_request(request)\n"
                "else:\n"
                f"    return legacy_{snake}.handle_request(request)\n"
                "```"
            )
            rollback = [
                f"Instant: set feature flag `{flag_name}` rollout percentage to `0%`",
                "Full: disable flag in `flags.yaml` (legacy implementation remains live)",
            ]

        return TrafficRoutingPlan(
            module_name=module_name,
            strategy_type=strat,
            flag_name=flag_name,
            stages=stages,
            monitoring_metrics=[
                "Error rate (HTTP 5xx / uncaught exceptions) <= legacy baseline",
                "Latency (p50, p95, p99) compared against legacy baseline",
                "Data parity / shadow diff mismatch rate == 0%",
                "Customer support tickets & guardrail SLO burn rate",
            ],
            rollback_procedure=rollback,
            code_snippet=snippet,
        )

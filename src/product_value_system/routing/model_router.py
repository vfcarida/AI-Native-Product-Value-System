"""
Complexity-aware ModelRouter selecting cost-effective models with fallback chains,
user overrides, YAML hot-reload, and cost-savings telemetry (EVO-026).
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from product_value_system.adapters.model_adapter import Message
from product_value_system.routing.complexity_classifier import (
    ComplexityClassifier,
    ComplexityLevel,
)
from product_value_system.routing.routing_policy import (
    DEFAULT_ROUTING_CONFIG,
    ModelSpec,
    RoutingPolicy,
)

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Selected model, provider, complexity metadata, and ordered fallback chain."""

    provider: str
    model: str
    complexity_level: ComplexityLevel
    estimated_cost_per_1k_tokens: float
    fallback_models: list[str]
    complexity_score: float = 0.0
    reasoning: str = ""
    fallback_specs: list[ModelSpec] = field(default_factory=list)
    primary_spec: ModelSpec | None = None
    is_override: bool = False
    is_shadow: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "complexity_level": self.complexity_level.value,
            "complexity_score": round(self.complexity_score, 4),
            "estimated_cost_per_1k_tokens": round(self.estimated_cost_per_1k_tokens, 6),
            "fallback_models": list(self.fallback_models),
            "reasoning": self.reasoning,
            "is_override": self.is_override,
            "is_shadow": self.is_shadow,
        }


class ModelRouter:
    """Route LLM requests to the most cost-effective model tier based on complexity."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        policy: RoutingPolicy | None = None,
        config_path: Path | str | None = None,
    ) -> None:
        self._config_path: Path | None = Path(config_path) if config_path else None
        if policy is not None:
            self.policy = policy
        elif self._config_path is not None and self._config_path.exists():
            self.policy = RoutingPolicy.from_yaml(self._config_path)
        else:
            self.policy = RoutingPolicy(config or DEFAULT_ROUTING_CONFIG)

        self.config: dict[str, Any] = self.policy.raw_config
        self.classifier = ComplexityClassifier(self.config)
        self.models_by_tier = self.policy.models_by_tier

        self._lock = threading.RLock()
        self._decisions_by_tier: Counter[str] = Counter()
        self._decisions_by_model: Counter[str] = Counter()
        self._total_decisions = 0
        self._fallback_count = 0
        self._routed_cost_usd = 0.0
        self._baseline_cost_usd = 0.0

    @classmethod
    def from_yaml(cls, path: Path | str) -> ModelRouter:
        """Load ModelRouter from a YAML configuration file (supports hot-reload)."""
        return cls(config_path=path)

    @property
    def enabled(self) -> bool:
        return self.policy.enabled

    @property
    def shadow_mode(self) -> bool:
        return self.policy.shadow_mode

    def reload(self) -> None:
        """Hot-reload routing policy from disk when initialized via ``config_path``."""
        if self._config_path is None:
            raise ValueError("Cannot reload ModelRouter without a config_path.")
        with self._lock:
            self.policy = RoutingPolicy.from_yaml(self._config_path)
            self.config = self.policy.raw_config
            self.models_by_tier = self.policy.models_by_tier
            self.classifier = ComplexityClassifier(self.config)

    def route(
        self,
        messages: Sequence[Message | dict[str, Any]],
        task_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        user_override_model: str | None = None,
    ) -> RoutingDecision:
        """Classify complexity and return the optimal ``RoutingDecision``."""
        if user_override_model:
            decision = self._create_decision_from_override(user_override_model)
            self._record_decision(decision)
            return decision

        complexity = self.classifier.classify(messages, task_type=task_type, metadata=metadata)
        effective_tier = self.policy.force_tier or complexity.level

        primary, fallback_specs = self.policy.select_models(effective_tier)
        decision = RoutingDecision(
            provider=primary.provider,
            model=primary.model,
            complexity_level=effective_tier,
            complexity_score=complexity.score,
            reasoning=complexity.reasoning,
            estimated_cost_per_1k_tokens=primary.cost_per_1m_input / 1000.0,
            fallback_models=[fb.model for fb in fallback_specs],
            fallback_specs=fallback_specs,
            primary_spec=primary,
            is_override=self.policy.force_tier is not None,
            is_shadow=self.policy.shadow_mode,
        )
        self._record_decision(decision)
        logger.debug(
            "Routed task (%s, score=%.2f) -> %s/%s (fallbacks=%s)",
            effective_tier.value,
            complexity.score,
            primary.provider,
            primary.model,
            decision.fallback_models,
        )
        return decision

    def _create_decision_from_override(self, model_id: str) -> RoutingDecision:
        found = self.policy.find_model(model_id)
        if found is None:
            raise ValueError(f"Model {model_id} not found in routing configuration")
        tier, spec = found
        return RoutingDecision(
            provider=spec.provider,
            model=spec.model,
            complexity_level=tier,
            complexity_score=0.0,
            reasoning=f"User override forced model '{model_id}'.",
            estimated_cost_per_1k_tokens=spec.cost_per_1m_input / 1000.0,
            fallback_models=[],
            fallback_specs=[],
            primary_spec=spec,
            is_override=True,
            is_shadow=False,
        )

    def _record_decision(self, decision: RoutingDecision) -> None:
        with self._lock:
            self._total_decisions += 1
            self._decisions_by_tier[decision.complexity_level.value] += 1
            self._decisions_by_model[decision.model] += 1

    def record_execution(
        self,
        *,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        used_fallback: bool = False,
    ) -> float:
        """Record actual execution cost against HIGH-tier baseline to quantify FinOps savings."""
        found = self.policy.find_model(model_id)
        baseline_spec = self.policy.baseline_high_spec()
        actual_spec = found[1] if found is not None else baseline_spec

        actual_cost = actual_spec.estimate_cost(input_tokens, output_tokens)
        baseline_cost = baseline_spec.estimate_cost(input_tokens, output_tokens)

        with self._lock:
            self._routed_cost_usd += actual_cost
            self._baseline_cost_usd += baseline_cost
            if used_fallback:
                self._fallback_count += 1
            if (
                self.policy.cost_tracking_enabled
                and self._routed_cost_usd > self.policy.alert_threshold_usd
            ):
                logger.warning(
                    "Routing cumulative cost $%.2f exceeded alert threshold $%.2f",
                    self._routed_cost_usd,
                    self.policy.alert_threshold_usd,
                )
        return actual_cost

    def get_metrics(self) -> dict[str, Any]:
        """Return routing distribution, fallback rate, and FinOps cost savings snapshot."""
        with self._lock:
            savings = max(0.0, self._baseline_cost_usd - self._routed_cost_usd)
            savings_pct = (
                (savings / self._baseline_cost_usd) * 100.0
                if self._baseline_cost_usd > 0
                else 0.0
            )
            fallback_rate = (
                self._fallback_count / self._total_decisions
                if self._total_decisions > 0
                else 0.0
            )
            return {
                "total_decisions": self._total_decisions,
                "decisions_by_tier": dict(self._decisions_by_tier),
                "decisions_by_model": dict(self._decisions_by_model),
                "fallback_count": self._fallback_count,
                "fallback_rate": round(fallback_rate, 4),
                "routed_cost_usd": round(self._routed_cost_usd, 6),
                "baseline_cost_usd": round(self._baseline_cost_usd, 6),
                "cost_savings_usd": round(savings, 6),
                "cost_savings_pct": round(savings_pct, 2),
                "alert_triggered": self._routed_cost_usd > self.policy.alert_threshold_usd,
            }

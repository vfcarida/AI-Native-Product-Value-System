"""
Model routing policy definitions, cost-sorted tier selection, and YAML hot-reload (EVO-026).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from product_value_system.routing.complexity_classifier import ComplexityLevel


@dataclass(frozen=True)
class ModelSpec:
    """Model tier entry with provider and per-1M-token input/output pricing."""

    provider: str
    model: str
    cost_per_1m_input: float
    cost_per_1m_output: float

    @property
    def blended_cost_per_1m(self) -> float:
        """Weighted blended cost assuming 75% input / 25% output token split."""
        return 0.75 * self.cost_per_1m_input + 0.25 * self.cost_per_1m_output

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Compute USD cost for exact input & output token counts."""
        return (
            (input_tokens / 1_000_000.0) * self.cost_per_1m_input
            + (output_tokens / 1_000_000.0) * self.cost_per_1m_output
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelSpec:
        return cls(
            provider=str(data["provider"]),
            model=str(data["model"]),
            cost_per_1m_input=float(data["cost_per_1m_input"]),
            cost_per_1m_output=float(data["cost_per_1m_output"]),
        )


DEFAULT_ROUTING_CONFIG: dict[str, Any] = {
    "enabled": True,
    "shadow_mode": False,
    "force_tier": None,
    "preferred_provider": None,
    "models": {
        "low_complexity": [
            {
                "provider": "anthropic",
                "model": "claude-3-haiku-20240307",
                "cost_per_1m_input": 0.25,
                "cost_per_1m_output": 1.25,
            },
            {
                "provider": "openai",
                "model": "gpt-3.5-turbo-0125",
                "cost_per_1m_input": 0.50,
                "cost_per_1m_output": 1.50,
            },
        ],
        "medium_complexity": [
            {
                "provider": "anthropic",
                "model": "claude-3-5-sonnet-20241022",
                "cost_per_1m_input": 3.00,
                "cost_per_1m_output": 15.00,
            },
            {
                "provider": "openai",
                "model": "gpt-4o",
                "cost_per_1m_input": 2.50,
                "cost_per_1m_output": 10.00,
            },
        ],
        "high_complexity": [
            {
                "provider": "openai",
                "model": "gpt-4-turbo-2024-04-09",
                "cost_per_1m_input": 10.00,
                "cost_per_1m_output": 30.00,
            },
            {
                "provider": "anthropic",
                "model": "claude-3-opus-20240229",
                "cost_per_1m_input": 15.00,
                "cost_per_1m_output": 75.00,
            },
        ],
    },
    "quality_check": {
        "enabled": True,
        "fallback_on_failure": True,
        "validation_method": "schema_compliance",
    },
    "cost_tracking": {
        "enabled": True,
        "alert_threshold_usd": 100.0,
    },
}


class RoutingPolicy:
    """Encapsulates model tiers, provider preferences, fallback chain, and rollout controls."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = config or DEFAULT_ROUTING_CONFIG
        cfg = raw.get("routing", raw)
        self.raw_config: dict[str, Any] = dict(cfg)

        self.enabled: bool = bool(cfg.get("enabled", True))
        self.shadow_mode: bool = bool(cfg.get("shadow_mode", False))
        force_raw = cfg.get("force_tier")
        self.force_tier: ComplexityLevel | None = (
            ComplexityLevel(str(force_raw).lower()) if force_raw else None
        )
        pref_raw = cfg.get("preferred_provider")
        self.preferred_provider: str | None = str(pref_raw).lower() if pref_raw else None

        models_cfg = cfg.get("models", DEFAULT_ROUTING_CONFIG["models"])
        self.models_by_tier: dict[ComplexityLevel, list[ModelSpec]] = {
            ComplexityLevel.LOW: [
                ModelSpec.from_dict(m) for m in models_cfg.get("low_complexity", [])
            ],
            ComplexityLevel.MEDIUM: [
                ModelSpec.from_dict(m) for m in models_cfg.get("medium_complexity", [])
            ],
            ComplexityLevel.HIGH: [
                ModelSpec.from_dict(m) for m in models_cfg.get("high_complexity", [])
            ],
        }

        qc = cfg.get("quality_check", {}) or {}
        self.quality_check_enabled: bool = bool(qc.get("enabled", True))
        self.fallback_on_failure: bool = bool(qc.get("fallback_on_failure", True))
        self.validation_method: str = str(qc.get("validation_method", "schema_compliance"))

        ct = cfg.get("cost_tracking", {}) or {}
        self.cost_tracking_enabled: bool = bool(ct.get("enabled", True))
        self.alert_threshold_usd: float = float(ct.get("alert_threshold_usd", 100.0))

    @classmethod
    def from_yaml(cls, path: Path | str) -> RoutingPolicy:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(data)

    def select_models(self, tier: ComplexityLevel) -> tuple[ModelSpec, list[ModelSpec]]:
        """Select cheapest primary model in ``tier`` plus ordered higher-tier fallbacks."""
        candidates = list(self.models_by_tier.get(tier, []))
        if not candidates:
            raise ValueError(f"No models configured for complexity tier: {tier.value}")

        def _sort_key(spec: ModelSpec) -> tuple[int, float]:
            provider_rank = (
                0
                if (self.preferred_provider and spec.provider.lower() == self.preferred_provider)
                else 1
            )
            return (provider_rank, spec.cost_per_1m_input)

        ordered = sorted(candidates, key=_sort_key)
        primary = ordered[0]
        fallbacks: list[ModelSpec] = list(ordered[1:])

        # Escalate to next complexity tier(s) as higher-tier fallbacks
        if tier == ComplexityLevel.LOW:
            med = sorted(self.models_by_tier.get(ComplexityLevel.MEDIUM, []), key=_sort_key)
            high = sorted(self.models_by_tier.get(ComplexityLevel.HIGH, []), key=_sort_key)
            fallbacks.extend(med[:1])
            fallbacks.extend(high[:1])
        elif tier == ComplexityLevel.MEDIUM:
            high = sorted(self.models_by_tier.get(ComplexityLevel.HIGH, []), key=_sort_key)
            fallbacks.extend(high[:1])

        # Deduplicate while preserving order
        seen = {primary.model}
        unique_fallbacks: list[ModelSpec] = []
        for fb in fallbacks:
            if fb.model not in seen:
                seen.add(fb.model)
                unique_fallbacks.append(fb)
        return primary, unique_fallbacks

    def find_model(self, model_id: str) -> tuple[ComplexityLevel, ModelSpec] | None:
        """Look up a ``ModelSpec`` and its tier by model identifier."""
        for tier, specs in self.models_by_tier.items():
            for spec in specs:
                if spec.model == model_id:
                    return tier, spec
        return None

    def baseline_high_spec(self) -> ModelSpec:
        """Return primary HIGH-tier model used as baseline for computing cost savings."""
        high_models = self.models_by_tier.get(ComplexityLevel.HIGH, [])
        if high_models:
            return min(high_models, key=lambda m: m.cost_per_1m_input)
        for specs in self.models_by_tier.values():
            if specs:
                return specs[0]
        return ModelSpec("openai", "gpt-4-turbo-2024-04-09", 10.0, 30.0)

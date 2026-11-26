"""
Contextual Bandit ML Model Router closing the online FinOps cost x quality
feedback loop (EVO-P3-11).

Uses Upper Confidence Bound (UCB1) partitioned by ``(complexity_tier, task_type)``
context buckets, optimizing a composite reward:

    reward = quality_score - cost_weight * normalized_cost - latency_weight * normalized_latency

Cold-start candidates are explored deterministically before converging onto the
highest-utility model for each task context.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass
class ArmStats:
    """Online empirical statistics for a single (context, model) arm."""

    context_key: str
    model_id: str
    pulls: int = 0
    total_reward: float = 0.0
    total_quality: float = 0.0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0

    @property
    def mean_reward(self) -> float:
        return self.total_reward / self.pulls if self.pulls > 0 else 0.0

    @property
    def avg_quality(self) -> float:
        return self.total_quality / self.pulls if self.pulls > 0 else 0.0

    @property
    def avg_cost_usd(self) -> float:
        return self.total_cost_usd / self.pulls if self.pulls > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.pulls if self.pulls > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_key": self.context_key,
            "model_id": self.model_id,
            "pulls": self.pulls,
            "mean_reward": round(self.mean_reward, 4),
            "avg_quality": round(self.avg_quality, 4),
            "avg_cost_usd": round(self.avg_cost_usd, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


@dataclass(frozen=True)
class BanditSelection:
    """Result of contextual bandit model ranking."""

    selected_model: str
    ranked_models: list[str]
    ucb_scores: dict[str, float]
    context_key: str
    was_exploration: bool


class ContextualBanditRouter:
    """
    Thread-safe UCB1 contextual bandit balancing LLM quality, USD cost, and latency.
    """

    def __init__(
        self,
        *,
        exploration_weight: float = 0.35,
        cost_penalty_weight: float = 0.25,
        latency_penalty_weight: float = 0.05,
        reference_cost_usd: float = 0.05,
        reference_latency_ms: float = 5000.0,
    ) -> None:
        self.exploration_weight = max(0.0, float(exploration_weight))
        self.cost_penalty_weight = max(0.0, float(cost_penalty_weight))
        self.latency_penalty_weight = max(0.0, float(latency_penalty_weight))
        self.reference_cost_usd = max(1e-6, float(reference_cost_usd))
        self.reference_latency_ms = max(1.0, float(reference_latency_ms))
        self._lock = threading.RLock()
        self._arms: dict[str, dict[str, ArmStats]] = {}

    @staticmethod
    def make_context_key(tier: str, task_type: str | None = None) -> str:
        norm_tier = tier.strip().lower() or "medium"
        norm_task = (task_type or "general").strip().lower() or "general"
        return f"{norm_tier}:{norm_task}"

    def compute_reward(
        self,
        *,
        quality_score: float,
        cost_usd: float,
        latency_ms: float = 0.0,
    ) -> float:
        """Compute bounded composite utility reward in [0.0, 1.0]."""
        q = max(0.0, min(1.0, float(quality_score)))
        norm_cost = min(1.0, max(0.0, float(cost_usd)) / self.reference_cost_usd)
        norm_latency = min(1.0, max(0.0, float(latency_ms)) / self.reference_latency_ms)
        raw = q - self.cost_penalty_weight * norm_cost - self.latency_penalty_weight * norm_latency
        return max(0.0, min(1.0, raw))

    def select_model(
        self,
        context_key: str,
        candidate_models: Sequence[str],
    ) -> BanditSelection:
        """Rank candidate models using UCB1 over observed outcomes in ``context_key``."""
        candidates = list(dict.fromkeys(candidate_models))
        if not candidates:
            raise ValueError("candidate_models must not be empty")

        with self._lock:
            ctx_arms = self._arms.setdefault(context_key, {})
            total_pulls = sum(ctx_arms[m].pulls for m in candidates if m in ctx_arms)

            # Cold-start deterministic exploration: try unpulled candidates first
            unpulled = [m for m in candidates if m not in ctx_arms or ctx_arms[m].pulls == 0]
            if unpulled:
                chosen = unpulled[0]
                scores = {
                    m: (float("inf") if m in unpulled else ctx_arms[m].mean_reward)
                    for m in candidates
                }
                ranked = [chosen] + [m for m in candidates if m != chosen]
                return BanditSelection(
                    selected_model=chosen,
                    ranked_models=ranked,
                    ucb_scores={
                        m: (1.0 if math.isinf(val) else round(val, 4))
                        for m, val in scores.items()
                    },
                    context_key=context_key,
                    was_exploration=True,
                )

            ucb_scores: dict[str, float] = {}
            log_n = math.log(max(2, total_pulls))
            for model_id in candidates:
                arm = ctx_arms[model_id]
                bonus = self.exploration_weight * math.sqrt((2.0 * log_n) / arm.pulls)
                ucb_scores[model_id] = arm.mean_reward + bonus

            ranked = sorted(candidates, key=lambda m: ucb_scores[m], reverse=True)
            best_mean_model = max(candidates, key=lambda m: ctx_arms[m].mean_reward)
            return BanditSelection(
                selected_model=ranked[0],
                ranked_models=ranked,
                ucb_scores={m: round(s, 4) for m, s in ucb_scores.items()},
                context_key=context_key,
                was_exploration=ranked[0] != best_mean_model,
            )

    def record_outcome(
        self,
        context_key: str,
        model_id: str,
        *,
        quality_score: float,
        cost_usd: float,
        latency_ms: float = 0.0,
    ) -> ArmStats:
        """Update online arm statistics after a validated model invocation."""
        reward = self.compute_reward(
            quality_score=quality_score,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
        with self._lock:
            ctx_arms = self._arms.setdefault(context_key, {})
            arm = ctx_arms.get(model_id)
            if arm is None:
                arm = ArmStats(context_key=context_key, model_id=model_id)
                ctx_arms[model_id] = arm
            arm.pulls += 1
            arm.total_reward += reward
            arm.total_quality += max(0.0, min(1.0, float(quality_score)))
            arm.total_cost_usd += max(0.0, float(cost_usd))
            arm.total_latency_ms += max(0.0, float(latency_ms))
            return arm

    def get_stats(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Export snapshot of all learned contextual bandit arms."""
        with self._lock:
            return {
                ctx: {model_id: arm.to_dict() for model_id, arm in arms.items()}
                for ctx, arms in self._arms.items()
            }

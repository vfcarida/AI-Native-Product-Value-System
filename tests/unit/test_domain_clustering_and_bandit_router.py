"""
Tests for Domain-Agnostic Theme Clustering and UCB1 Contextual Bandit ML
ModelRouter (EVO-P3-11).
"""

from __future__ import annotations

import pytest

from product_value_system.adapters.embedding_adapter import LocalHashEmbeddingAdapter
from product_value_system.discovery.insight_extractor import PainPoint, PainPointSeverity
from product_value_system.discovery.theme_clusterer import ThemeClusterer
from product_value_system.routing.bandit_router import ContextualBanditRouter
from product_value_system.routing.model_router import ModelRouter


@pytest.mark.asyncio
async def test_domain_agnostic_clustering_works_on_healthcare_and_devtools() -> None:
    """Domain-agnostic TF-IDF clustering separates non-fintech domains cleanly."""
    pain_points = [
        PainPoint(
            description="Radiology DICOM scan upload crashes on MRI imaging workstation",
            severity=PainPointSeverity.CRITICAL,
            frequency=1,
            quotes=["Radiology DICOM scan upload crashes every shift"],
            interview_id="hosp-1",
            confidence=0.95,
        ),
        PainPoint(
            description="MRI imaging workstation crashes during Radiology DICOM scan upload",
            severity=PainPointSeverity.HIGH,
            frequency=1,
            quotes=["DICOM MRI upload keeps crashing"],
            interview_id="hosp-2",
            confidence=0.90,
        ),
        PainPoint(
            description="Kubernetes Helm chart linting fails on custom CRD schema validation",
            severity=PainPointSeverity.MEDIUM,
            frequency=1,
            quotes=["Helm chart linting rejects custom CRD schema"],
            interview_id="dev-1",
            confidence=0.88,
        ),
        PainPoint(
            description="Custom CRD schema validation breaks Kubernetes Helm chart linting",
            severity=PainPointSeverity.MEDIUM,
            frequency=1,
            quotes=["Kubernetes Helm CRD schema lint failure"],
            interview_id="dev-2",
            confidence=0.85,
        ),
    ]

    clusterer = ThemeClusterer(domain_agnostic=True)
    themes = await clusterer.cluster_pain_points(
        pain_points,
        similarity_threshold=0.55,
        min_samples=2,
    )
    assert len(themes) == 2
    assert all(t.frequency == 2 for t in themes)

    # Also verifies direct EmbeddingAdapter injection
    adapter_clusterer = ThemeClusterer(
        embedding_adapter=LocalHashEmbeddingAdapter(dimension=128),
        domain_agnostic=True,
    )
    adapter_themes = await adapter_clusterer.cluster_pain_points(
        pain_points,
        similarity_threshold=0.55,
        min_samples=2,
    )
    assert len(adapter_themes) == 2


def test_contextual_bandit_explores_then_converges_on_cost_effective_winner() -> None:
    """UCB1 contextual bandit explores unpulled arms then converges to high-utility arm."""
    bandit = ContextualBanditRouter(exploration_weight=0.25, cost_penalty_weight=0.30)
    candidates = ["expensive-model", "cheap-high-quality", "cheap-low-quality"]
    ctx = bandit.make_context_key("medium", "code_review")

    # 1. Cold-start explores each candidate deterministically
    first = bandit.select_model(ctx, candidates)
    assert first.selected_model == "expensive-model"
    assert first.was_exploration is True
    bandit.record_outcome(ctx, "expensive-model", quality_score=0.92, cost_usd=0.045)

    second = bandit.select_model(ctx, candidates)
    assert second.selected_model == "cheap-high-quality"
    bandit.record_outcome(ctx, "cheap-high-quality", quality_score=0.91, cost_usd=0.002)

    third = bandit.select_model(ctx, candidates)
    assert third.selected_model == "cheap-low-quality"
    bandit.record_outcome(ctx, "cheap-low-quality", quality_score=0.40, cost_usd=0.001)

    # 2. Train with additional empirical observations
    for _ in range(15):
        bandit.record_outcome(ctx, "expensive-model", quality_score=0.92, cost_usd=0.045)
        bandit.record_outcome(ctx, "cheap-high-quality", quality_score=0.91, cost_usd=0.002)
        bandit.record_outcome(ctx, "cheap-low-quality", quality_score=0.40, cost_usd=0.001)

    converged = bandit.select_model(ctx, candidates)
    assert converged.selected_model == "cheap-high-quality"
    assert converged.was_exploration is False
    assert converged.ranked_models[-1] == "cheap-low-quality"


def test_model_router_integrates_contextual_bandit_feedback() -> None:
    """ModelRouter routes with bandit candidate ranking and exposes bandit_stats metrics."""
    bandit = ContextualBanditRouter(exploration_weight=0.1)
    router = ModelRouter(bandit=bandit)

    # Inspect candidates for LOW tier and reward the fallback higher than primary
    low_primary, low_fallbacks = router.policy.select_models(
        router.classifier.classify([{"role": "user", "content": "format json"}]).level
    )
    if low_fallbacks:
        preferred = low_fallbacks[0].model
        all_candidates = [low_primary.model, *(fb.model for fb in low_fallbacks)]
        for _ in range(8):
            for candidate_id in all_candidates:
                is_pref = candidate_id == preferred
                router.record_quality_feedback(
                    tier="low",
                    task_type="format_conversion",
                    model_id=candidate_id,
                    quality_score=0.96 if is_pref else 0.45,
                    cost_usd=0.001 if is_pref else 0.02,
                )

        decision = router.route(
            [{"role": "user", "content": "convert CSV to JSON"}],
            task_type="format_conversion",
        )
        assert decision.model == preferred
        assert decision.bandit_context_key == "low:format_conversion"
        assert "bandit(" in decision.reasoning

        metrics = router.get_metrics()
        assert "low:format_conversion" in metrics["bandit_stats"]

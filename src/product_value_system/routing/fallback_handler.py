"""
Quality-based automatic fallback handler escalating from cheaper tiers to stronger models
when response quality checks fail or transient model errors occur (EVO-026).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any

from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelError,
    ModelResponse,
)
from product_value_system.routing.model_router import ModelRouter, RoutingDecision
from product_value_system.routing.quality_validator import (
    QualityValidationResult,
    QualityValidator,
)

logger = logging.getLogger(__name__)

AdapterFactory = Callable[[str, str], ModelAdapter]


class QualityCheckFailedError(Exception):
    """Raised when primary and all fallback model attempts fail quality validation."""

    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


class FallbackHandler:
    """Execute routed LLM calls with automatic higher-tier fallback on quality failures."""

    def __init__(
        self,
        router: ModelRouter,
        quality_validator: QualityValidator | None = None,
    ) -> None:
        self.router = router
        self.quality_validator = quality_validator or QualityValidator()
        self.fallback_attempts: dict[str, list[dict[str, Any]]] = {}

    async def execute_with_fallback(
        self,
        messages: Sequence[Message | dict[str, Any]],
        routing_decision: RoutingDecision,
        adapter_factory: AdapterFactory,
        *,
        max_attempts: int = 3,
        task_id: str | None = None,
        validation_context: dict[str, Any] | None = None,
        **invoke_kwargs: Any,
    ) -> ModelResponse:
        """Execute primary routed model, escalating through fallbacks on quality/model failure."""
        candidate_chain: list[tuple[str, str]] = [
            (routing_decision.provider, routing_decision.model)
        ]
        if routing_decision.fallback_specs:
            for spec in routing_decision.fallback_specs:
                candidate_chain.append((spec.provider, spec.model))
        else:
            for fb_model in routing_decision.fallback_models:
                found = self.router.policy.find_model(fb_model)
                fb_provider = found[1].provider if found else routing_decision.provider
                candidate_chain.append((fb_provider, fb_model))

        attempts: list[dict[str, Any]] = []
        limit = min(max(1, max_attempts), len(candidate_chain))
        if not self.router.policy.fallback_on_failure:
            limit = 1

        for attempt_idx in range(limit):
            provider, model_id = candidate_chain[attempt_idx]
            adapter = adapter_factory(provider, model_id)
            logger.info(
                "Routing attempt %d/%d using %s/%s", attempt_idx + 1, limit, provider, model_id
            )

            try:
                response = await adapter.invoke(list(messages), **invoke_kwargs)
            except ModelError as exc:
                logger.warning("Model error on %s/%s: %s", provider, model_id, exc)
                attempts.append(
                    {
                        "provider": provider,
                        "model": model_id,
                        "error": str(exc),
                        "quality_result": QualityValidationResult(
                            passed=False,
                            score=0.0,
                            reason=f"ModelError: {exc}",
                            method="execution",
                        ),
                    }
                )
                continue

            if not response.model:
                response.model = model_id

            quality_result = await self.quality_validator.validate(
                response,
                context=validation_context,
            )

            in_tok = response.usage.input_tokens if response.usage else 0
            out_tok = response.usage.output_tokens if response.usage else 0

            if quality_result.passed:
                self.router.record_execution(
                    model_id=model_id,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    used_fallback=attempt_idx > 0,
                )
                if task_id:
                    self.fallback_attempts[task_id] = attempts
                return response

            logger.warning(
                "Quality check failed for %s/%s: %s",
                provider,
                model_id,
                quality_result.reason,
            )
            self.router.record_execution(
                model_id=model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                used_fallback=False,
            )
            attempts.append(
                {
                    "provider": provider,
                    "model": model_id,
                    "response": response,
                    "quality_result": quality_result,
                }
            )

        if task_id:
            self.fallback_attempts[task_id] = attempts
        raise QualityCheckFailedError(
            f"All {len(attempts)} attempt(s) failed quality check.",
            attempts=attempts,
        )

"""
Base adapter protocol export and transparent complexity-routed adapter wrapper (EVO-026).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelChunk,
    ModelResponse,
    Tool,
)
from product_value_system.routing.fallback_handler import AdapterFactory, FallbackHandler
from product_value_system.routing.model_router import ModelRouter, RoutingDecision
from product_value_system.routing.quality_validator import QualityValidator


class RoutedModelAdapter:
    """Transparent ModelAdapter wrapper routing each invoke() call by task complexity."""

    def __init__(
        self,
        router: ModelRouter,
        adapter_factory: AdapterFactory,
        *,
        quality_validator: QualityValidator | None = None,
        default_adapter: ModelAdapter | None = None,
        max_fallback_attempts: int = 3,
    ) -> None:
        self.router = router
        self.adapter_factory = adapter_factory
        self.quality_validator = quality_validator
        self.default_adapter = default_adapter
        self.max_fallback_attempts = max_fallback_attempts
        self.fallback_handler = FallbackHandler(router, quality_validator)
        self.last_decision: RoutingDecision | None = None
        self.model: str = "routed"

    async def invoke(
        self,
        messages: list[Message] | list[dict[str, Any]],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Route prompt to optimal model tier and optionally validate with fallback."""
        task_type = kwargs.pop("task_type", None)
        metadata = kwargs.pop("routing_metadata", None)
        user_override = kwargs.pop("user_override_model", None)

        if not self.router.enabled and self.default_adapter is not None:
            return await self.default_adapter.invoke(
                messages=messages,  # type: ignore[arg-type]
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        decision = self.router.route(
            messages=messages,
            task_type=task_type,
            metadata=metadata,
            user_override_model=user_override,
        )
        self.last_decision = decision
        self.model = decision.model

        if decision.is_shadow and self.default_adapter is not None:
            return await self.default_adapter.invoke(
                messages=messages,  # type: ignore[arg-type]
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        invoke_kwargs: dict[str, Any] = {
            "tools": tools,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs,
        }

        if self.quality_validator is not None and self.router.policy.quality_check_enabled:
            return await self.fallback_handler.execute_with_fallback(
                messages=messages,
                routing_decision=decision,
                adapter_factory=self.adapter_factory,
                max_attempts=self.max_fallback_attempts,
                **invoke_kwargs,
            )

        adapter = self.adapter_factory(decision.provider, decision.model)
        response = await adapter.invoke(messages, **invoke_kwargs)  # type: ignore[arg-type]
        if not response.model:
            response.model = decision.model
        in_tok = response.usage.input_tokens if response.usage else 0
        out_tok = response.usage.output_tokens if response.usage else 0
        self.router.record_execution(
            model_id=response.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            used_fallback=False,
        )
        return response

    async def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """Route streaming invocation to the selected tier's model adapter."""
        decision = self.router.route(messages=messages)
        self.last_decision = decision
        self.model = decision.model
        adapter = self.adapter_factory(decision.provider, decision.model)
        async for chunk in adapter.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield chunk


__all__ = ["ModelAdapter", "RoutedModelAdapter"]

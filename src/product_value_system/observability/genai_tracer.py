"""
OpenTelemetry GenAI tracer with v1.28+ semantic convention spans and events (EVO-021).

Supports all 4 standard operation types:
- ``chat`` (``ChatCompletionSpan``)
- ``completion`` (``TextCompletionSpan``)
- ``embedding`` (``EmbeddingSpan``)
- ``rerank`` (``RerankSpan``)
"""

from __future__ import annotations

import time
from types import TracebackType
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from product_value_system.observability.genai_conventions import (
    GenAIAttributes,
    GenAIEvents,
    GenAIOperation,
    infer_genai_system,
)
from product_value_system.observability.genai_metrics import GenAIMetrics
from product_value_system.observability.sanitization import sanitize_string

_OPTIONAL_REQUEST_PARAMS = {
    "top_p": GenAIAttributes.REQUEST_TOP_P,
    "presence_penalty": GenAIAttributes.REQUEST_PRESENCE_PENALTY,
    "frequency_penalty": GenAIAttributes.REQUEST_FREQUENCY_PENALTY,
    "stop_sequences": GenAIAttributes.REQUEST_STOP_SEQUENCES,
    "tool_call_count": GenAIAttributes.REQUEST_TOOL_CALL_COUNT,
}


class _BaseGenAISpan:
    """Shared lifecycle, error handling, and metric recording for GenAI spans."""

    def __init__(
        self,
        *,
        tracer: trace.Tracer,
        operation_name: str,
        genai_operation: GenAIOperation,
        model: str,
        system: str | None = None,
        metrics: GenAIMetrics | None = None,
        sanitize_content: bool = False,
    ) -> None:
        self.tracer = tracer
        self.operation_name = operation_name
        self.genai_operation = genai_operation
        self.model = model
        self.system = system or infer_genai_system(model)
        self.metrics = metrics
        self.sanitize_content = sanitize_content
        self.span: trace.Span | None = None
        self._ctx_token: object | None = None
        self._start_time: float = 0.0
        self._finish_reason: str = "stop"

    def _start_span(self, attributes: dict[str, Any]) -> trace.Span:
        self._start_time = time.perf_counter()
        self.span = self.tracer.start_span(self.operation_name, attributes=attributes)
        self._ctx_token = otel_context.attach(trace.set_span_in_context(self.span))
        return self.span

    def _clean(self, text: str) -> str:
        return sanitize_string(text) if self.sanitize_content else text

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        duration = max(0.0, time.perf_counter() - self._start_time)
        try:
            if self.span is None:
                return
            if exc_type is not None:
                self.span.set_status(Status(StatusCode.ERROR, str(exc_val)))
                self.span.set_attribute("error.type", exc_type.__name__)
                if exc_val is not None:
                    self.span.record_exception(exc_val)
                if self.metrics is not None:
                    self.metrics.record_error(self.system, exc_type.__name__)
            if self.metrics is not None:
                self.metrics.record_operation_duration(
                    system=self.system,
                    operation_name=self.genai_operation.value,
                    duration_seconds=duration,
                    finish_reason="error" if exc_type is not None else self._finish_reason,
                )
            self.span.end()
        finally:
            if self._ctx_token is not None:
                otel_context.detach(self._ctx_token)  # type: ignore[arg-type]
                self._ctx_token = None


class ChatCompletionSpan(_BaseGenAISpan):
    """Context manager for tracing ``chat`` completion operations."""

    def __init__(
        self,
        tracer: trace.Tracer,
        operation_name: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        system: str | None = None,
        metrics: GenAIMetrics | None = None,
        sanitize_content: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tracer=tracer,
            operation_name=operation_name,
            genai_operation=GenAIOperation.CHAT,
            model=model,
            system=system,
            metrics=metrics,
            sanitize_content=sanitize_content,
        )
        self.messages = messages
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.kwargs = kwargs

    def __enter__(self) -> ChatCompletionSpan:
        attrs: dict[str, Any] = {
            GenAIAttributes.SYSTEM: self.system,
            GenAIAttributes.REQUEST_MODEL: self.model,
            GenAIAttributes.OPERATION_NAME: self.genai_operation.value,
            GenAIAttributes.REQUEST_TEMPERATURE: float(self.temperature),
        }
        if self.max_tokens is not None:
            attrs[GenAIAttributes.REQUEST_MAX_TOKENS] = int(self.max_tokens)
        for key, attr_name in _OPTIONAL_REQUEST_PARAMS.items():
            if key in self.kwargs and self.kwargs[key] is not None:
                attrs[attr_name] = self.kwargs[key]

        self._start_span(attrs)
        self._record_prompt_event()
        return self

    def _record_prompt_event(self) -> None:
        if self.span is None:
            return
        event_attrs: dict[str, Any] = {}
        for idx, msg in enumerate(self.messages):
            event_attrs[f"gen_ai.prompt.{idx}.role"] = str(msg.get("role", "user"))
            event_attrs[f"gen_ai.prompt.{idx}.content"] = self._clean(str(msg.get("content", "")))
        self.span.add_event(name=GenAIEvents.CONTENT_PROMPT, attributes=event_attrs)

    def record_completion(
        self,
        response_id: str,
        completion: str,
        prompt_tokens: int,
        completion_tokens: int,
        finish_reason: str = "stop",
        response_model: str | None = None,
        cost_usd: float | None = None,
        tool_call_count: int | None = None,
    ) -> None:
        """Record standard response attributes, token usage, cost, and completion event."""
        if self.span is None:
            return
        self._finish_reason = finish_reason
        actual_model = response_model or self.model
        total_tokens = int(prompt_tokens) + int(completion_tokens)

        self.span.set_attribute(GenAIAttributes.RESPONSE_ID, response_id)
        self.span.set_attribute(GenAIAttributes.RESPONSE_MODEL, actual_model)
        self.span.set_attribute(GenAIAttributes.RESPONSE_FINISH_REASON, finish_reason)
        self.span.set_attribute(GenAIAttributes.USAGE_PROMPT_TOKENS, int(prompt_tokens))
        self.span.set_attribute(GenAIAttributes.USAGE_COMPLETION_TOKENS, int(completion_tokens))
        self.span.set_attribute(GenAIAttributes.USAGE_TOTAL_TOKENS, total_tokens)
        # Keep input_tokens/output_tokens aliases populated for backward compatibility
        self.span.set_attribute(GenAIAttributes.USAGE_INPUT_TOKENS, int(prompt_tokens))
        self.span.set_attribute(GenAIAttributes.USAGE_OUTPUT_TOKENS, int(completion_tokens))

        if cost_usd is not None:
            self.span.set_attribute(GenAIAttributes.COST_USD, float(cost_usd))
        if tool_call_count is not None:
            self.span.set_attribute(GenAIAttributes.RESPONSE_TOOL_CALL_COUNT, int(tool_call_count))

        self.span.add_event(
            name=GenAIEvents.CONTENT_COMPLETION,
            attributes={"gen_ai.completion": self._clean(completion)},
        )

        if self.metrics is not None:
            self.metrics.record_token_usage(self.system, actual_model, "prompt", int(prompt_tokens))
            self.metrics.record_token_usage(
                self.system, actual_model, "completion", int(completion_tokens)
            )
            if cost_usd is not None:
                self.metrics.record_cost(self.system, actual_model, float(cost_usd))

    def record_tool_call(self, tool_name: str, tool_input: str) -> None:
        """Emit standard ``gen_ai.tool.call`` span event."""
        if self.span is None:
            return
        self.span.add_event(
            name=GenAIEvents.TOOL_CALL,
            attributes={
                "gen_ai.tool.name": tool_name,
                "gen_ai.tool.input": self._clean(tool_input),
            },
        )

    def record_tool_result(self, tool_name: str, tool_output: str) -> None:
        """Emit standard ``gen_ai.tool.result`` span event."""
        if self.span is None:
            return
        self.span.add_event(
            name=GenAIEvents.TOOL_RESULT,
            attributes={
                "gen_ai.tool.name": tool_name,
                "gen_ai.tool.output": self._clean(tool_output),
            },
        )


class TextCompletionSpan(_BaseGenAISpan):
    """Context manager for tracing single-prompt ``completion`` operations."""

    def __init__(
        self,
        tracer: trace.Tracer,
        operation_name: str,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        system: str | None = None,
        metrics: GenAIMetrics | None = None,
        sanitize_content: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            tracer=tracer,
            operation_name=operation_name,
            genai_operation=GenAIOperation.COMPLETION,
            model=model,
            system=system,
            metrics=metrics,
            sanitize_content=sanitize_content,
        )
        self.prompt = prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.kwargs = kwargs

    def __enter__(self) -> TextCompletionSpan:
        attrs: dict[str, Any] = {
            GenAIAttributes.SYSTEM: self.system,
            GenAIAttributes.REQUEST_MODEL: self.model,
            GenAIAttributes.OPERATION_NAME: self.genai_operation.value,
            GenAIAttributes.REQUEST_TEMPERATURE: float(self.temperature),
        }
        if self.max_tokens is not None:
            attrs[GenAIAttributes.REQUEST_MAX_TOKENS] = int(self.max_tokens)
        span = self._start_span(attrs)
        span.add_event(
            name=GenAIEvents.CONTENT_PROMPT,
            attributes={"gen_ai.prompt": self._clean(self.prompt)},
        )
        return self

    def record_completion(
        self,
        response_id: str,
        completion: str,
        prompt_tokens: int,
        completion_tokens: int,
        finish_reason: str = "stop",
        response_model: str | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Record completion attributes, token counts, and event."""
        if self.span is None:
            return
        self._finish_reason = finish_reason
        actual_model = response_model or self.model
        self.span.set_attribute(GenAIAttributes.RESPONSE_ID, response_id)
        self.span.set_attribute(GenAIAttributes.RESPONSE_MODEL, actual_model)
        self.span.set_attribute(GenAIAttributes.RESPONSE_FINISH_REASON, finish_reason)
        self.span.set_attribute(GenAIAttributes.USAGE_PROMPT_TOKENS, int(prompt_tokens))
        self.span.set_attribute(GenAIAttributes.USAGE_COMPLETION_TOKENS, int(completion_tokens))
        self.span.set_attribute(
            GenAIAttributes.USAGE_TOTAL_TOKENS, int(prompt_tokens) + int(completion_tokens)
        )
        if cost_usd is not None:
            self.span.set_attribute(GenAIAttributes.COST_USD, float(cost_usd))
        self.span.add_event(
            name=GenAIEvents.CONTENT_COMPLETION,
            attributes={"gen_ai.completion": self._clean(completion)},
        )
        if self.metrics is not None:
            self.metrics.record_token_usage(self.system, actual_model, "prompt", int(prompt_tokens))
            self.metrics.record_token_usage(
                self.system, actual_model, "completion", int(completion_tokens)
            )
            if cost_usd is not None:
                self.metrics.record_cost(self.system, actual_model, float(cost_usd))


class EmbeddingSpan(_BaseGenAISpan):
    """Context manager for tracing ``embedding`` operations."""

    def __init__(
        self,
        tracer: trace.Tracer,
        operation_name: str,
        model: str,
        input_text: str,
        *,
        system: str | None = None,
        metrics: GenAIMetrics | None = None,
        sanitize_content: bool = False,
    ) -> None:
        super().__init__(
            tracer=tracer,
            operation_name=operation_name,
            genai_operation=GenAIOperation.EMBEDDING,
            model=model,
            system=system,
            metrics=metrics,
            sanitize_content=sanitize_content,
        )
        self.input_text = input_text

    def __enter__(self) -> EmbeddingSpan:
        span = self._start_span(
            {
                GenAIAttributes.SYSTEM: self.system,
                GenAIAttributes.REQUEST_MODEL: self.model,
                GenAIAttributes.OPERATION_NAME: self.genai_operation.value,
            }
        )
        span.add_event(
            name=GenAIEvents.CONTENT_PROMPT,
            attributes={"gen_ai.prompt": self._clean(self.input_text)},
        )
        return self

    def record_embedding(
        self,
        dimensions: int,
        token_count: int,
        *,
        cost_usd: float | None = None,
    ) -> None:
        """Record embedding output dimensions and token usage."""
        if self.span is None:
            return
        self.span.set_attribute(GenAIAttributes.EMBEDDING_DIMENSIONS, int(dimensions))
        self.span.set_attribute(GenAIAttributes.USAGE_PROMPT_TOKENS, int(token_count))
        self.span.set_attribute(GenAIAttributes.USAGE_TOTAL_TOKENS, int(token_count))
        if cost_usd is not None:
            self.span.set_attribute(GenAIAttributes.COST_USD, float(cost_usd))
        if self.metrics is not None:
            self.metrics.record_token_usage(self.system, self.model, "prompt", int(token_count))
            if cost_usd is not None:
                self.metrics.record_cost(self.system, self.model, float(cost_usd))


class RerankSpan(_BaseGenAISpan):
    """Context manager for tracing ``rerank`` retrieval operations."""

    def __init__(
        self,
        tracer: trace.Tracer,
        operation_name: str,
        model: str,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
        system: str | None = None,
        metrics: GenAIMetrics | None = None,
        sanitize_content: bool = False,
    ) -> None:
        super().__init__(
            tracer=tracer,
            operation_name=operation_name,
            genai_operation=GenAIOperation.RERANK,
            model=model,
            system=system,
            metrics=metrics,
            sanitize_content=sanitize_content,
        )
        self.query = query
        self.documents = documents
        self.top_n = top_n

    def __enter__(self) -> RerankSpan:
        attrs: dict[str, Any] = {
            GenAIAttributes.SYSTEM: self.system,
            GenAIAttributes.REQUEST_MODEL: self.model,
            GenAIAttributes.OPERATION_NAME: self.genai_operation.value,
            GenAIAttributes.RERANK_DOCUMENT_COUNT: len(self.documents),
        }
        if self.top_n is not None:
            attrs[GenAIAttributes.RERANK_TOP_N] = int(self.top_n)
        span = self._start_span(attrs)
        span.add_event(
            name=GenAIEvents.CONTENT_PROMPT,
            attributes={"gen_ai.prompt": self._clean(self.query)},
        )
        return self

    def record_rerank(
        self,
        *,
        returned_count: int,
        token_count: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        """Record reranking result count and optional token/cost usage."""
        if self.span is None:
            return
        self.span.set_attribute(GenAIAttributes.RESPONSE_MODEL, self.model)
        self.span.set_attribute(GenAIAttributes.RESPONSE_FINISH_REASON, "stop")
        self.span.add_event(
            name=GenAIEvents.CONTENT_COMPLETION,
            attributes={"gen_ai.completion": f"reranked_top_{returned_count}"},
        )
        if token_count is not None:
            self.span.set_attribute(GenAIAttributes.USAGE_PROMPT_TOKENS, int(token_count))
            if self.metrics is not None:
                self.metrics.record_token_usage(self.system, self.model, "prompt", int(token_count))
        if cost_usd is not None:
            self.span.set_attribute(GenAIAttributes.COST_USD, float(cost_usd))
            if self.metrics is not None:
                self.metrics.record_cost(self.system, self.model, float(cost_usd))


class GenAITracer:
    """High-level OpenTelemetry tracer compliant with GenAI v1.28+ semantic conventions."""

    def __init__(
        self,
        tracer: trace.Tracer | None = None,
        *,
        metrics: GenAIMetrics | None = None,
        sanitize_content: bool = False,
    ) -> None:
        self.tracer = tracer or trace.get_tracer("product_value_system.observability.genai")
        self.metrics = metrics
        self.sanitize_content = sanitize_content

    def trace_chat_completion(
        self,
        operation_name: str,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        system: str | None = None,
        **kwargs: Any,
    ) -> ChatCompletionSpan:
        """Trace a chat completion operation (``gen_ai.operation.name = 'chat'``)."""
        return ChatCompletionSpan(
            tracer=self.tracer,
            operation_name=operation_name,
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            metrics=self.metrics,
            sanitize_content=self.sanitize_content,
            **kwargs,
        )

    def trace_text_completion(
        self,
        operation_name: str,
        model: str,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        system: str | None = None,
        **kwargs: Any,
    ) -> TextCompletionSpan:
        """Trace a text completion operation (``gen_ai.operation.name = 'completion'``)."""
        return TextCompletionSpan(
            tracer=self.tracer,
            operation_name=operation_name,
            model=model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            metrics=self.metrics,
            sanitize_content=self.sanitize_content,
            **kwargs,
        )

    def trace_embedding(
        self,
        operation_name: str,
        model: str,
        input_text: str,
        *,
        system: str | None = None,
    ) -> EmbeddingSpan:
        """Trace an embedding operation (``gen_ai.operation.name = 'embedding'``)."""
        return EmbeddingSpan(
            tracer=self.tracer,
            operation_name=operation_name,
            model=model,
            input_text=input_text,
            system=system,
            metrics=self.metrics,
            sanitize_content=self.sanitize_content,
        )

    def trace_rerank(
        self,
        operation_name: str,
        model: str,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
        system: str | None = None,
    ) -> RerankSpan:
        """Trace a rerank operation (``gen_ai.operation.name = 'rerank'``)."""
        return RerankSpan(
            tracer=self.tracer,
            operation_name=operation_name,
            model=model,
            query=query,
            documents=documents,
            top_n=top_n,
            system=system,
            metrics=self.metrics,
            sanitize_content=self.sanitize_content,
        )

# How-To: Integrate a New Model Provider

**Goal**: Add a new LLM provider adapter implementing the `ModelAdapter` protocol.

**Time**: 30-45 minutes

**Prerequisites**:
- Familiarity with Python `typing.Protocol` and `async`/`await`
- Understanding of [ModelAdapter Interface](../../src/product_value_system/adapters/model_adapter.py)
- Understanding of [Observability API](../reference/api/observability.md)

---

## Step 1: Review the `ModelAdapter` Protocol

All model adapters must implement the `@runtime_checkable` `ModelAdapter` protocol in `src/product_value_system/adapters/model_adapter.py`:

- `async def invoke(messages, tools=None, temperature=0.7, max_tokens=None, **kwargs) -> ModelResponse`
- `def stream(messages, tools=None, temperature=0.7, max_tokens=None, **kwargs) -> AsyncIterator[ModelChunk]`

Adapters must translate provider-specific exceptions into `RateLimitError` or `ModelError`.

---

## Step 2: Create the Provider Adapter Class

Create: `src/product_value_system/adapters/anthropic_adapter.py`

```python
"""
Anthropic Claude model adapter implementation.
"""

from collections.abc import AsyncIterator
import os
from typing import Any

from product_value_system.adapters.model_adapter import (
    Message,
    ModelChunk,
    ModelError,
    ModelResponse,
    RateLimitError,
    Role,
    Tool,
    Usage,
)


class AnthropicModelAdapter:
    """
    Adapter for Anthropic Claude models implementing ModelAdapter protocol.
    """

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-latest",
        api_key: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_retries = max_retries
        if not self.api_key:
            raise ModelError("ANTHROPIC_API_KEY is required")

    async def invoke(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """
        Invoke Anthropic model with messages.
        """
        system_prompt, formatted_messages = self._format_messages(messages)

        try:
            # Replace with actual AsyncAnthropic SDK call if installed
            response_text = await self._call_provider(
                system_prompt=system_prompt,
                messages=formatted_messages,
                temperature=temperature,
                max_tokens=max_tokens or 4096,
            )
            input_tokens = sum(len(m.content.split()) for m in messages)
            output_tokens = len(response_text.split())

            return ModelResponse(
                content=response_text,
                tool_calls=None,
                usage=Usage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ),
                finish_reason="stop",
                model=self.model,
            )
        except RateLimitError:
            raise
        except Exception as exc:
            raise ModelError(f"Anthropic invocation failed: {exc}") from exc

    async def stream(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ModelChunk]:
        """
        Stream response chunks from Anthropic model.
        """
        response = await self.invoke(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        yield ModelChunk(content=response.content, finish_reason="stop")

    def _format_messages(
        self, messages: list[Message]
    ) -> tuple[str, list[dict[str, str]]]:
        """Separate system prompt from user/assistant conversation messages."""
        system_parts: list[str] = []
        conversation: list[dict[str, str]] = []

        for msg in messages:
            if msg.role == Role.SYSTEM:
                system_parts.append(msg.content)
            else:
                conversation.append({"role": msg.role.value, "content": msg.content})

        return "\n".join(system_parts), conversation

    async def _call_provider(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Execute underlying HTTP/SDK call."""
        return f"Processed {len(messages)} messages with {self.model}"
```

---

## Step 3: Register Pricing in `CostTracker`

Update `MODEL_PRICING` in `src/product_value_system/observability/cost_tracker.py` so token usage is automatically converted to USD cost:

```python
MODEL_PRICING = {
    "gpt-4": {
        "input": Decimal("0.03") / Decimal("1000"),
        "output": Decimal("0.06") / Decimal("1000"),
    },
    "claude-3-5-sonnet-latest": {
        "input": Decimal("0.003") / Decimal("1000"),
        "output": Decimal("0.015") / Decimal("1000"),
    },
}
```

---

## Step 4: Export Adapter in Package

Update `src/product_value_system/adapters/__init__.py`:

```python
from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelChunk,
    ModelError,
    ModelResponse,
    RateLimitError,
    Role,
    Tool,
    ToolCall,
    Usage,
)
from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.openai_adapter import OpenAIModelAdapter
```

---

## Step 5: Write Unit Tests

Create: `tests/unit/test_custom_model_adapter.py`

```python
"""
Unit tests for custom model adapter protocol conformance.
"""

import pytest

from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    Role,
)


@pytest.mark.asyncio
async def test_adapter_implements_protocol() -> None:
    """Verify custom adapter satisfies ModelAdapter protocol."""
    adapter = AnthropicModelAdapter(api_key="test-key-placeholder")
    assert isinstance(adapter, ModelAdapter)

    response = await adapter.invoke(
        messages=[
            Message(role=Role.SYSTEM, content="You are a helpful assistant."),
            Message(role=Role.USER, content="Hello"),
        ],
        temperature=0.0,
    )

    assert response.content
    assert response.usage is not None
    assert response.usage.total_tokens > 0
```

Run tests:

```bash
pytest tests/unit/test_custom_model_adapter.py -v
```

---

## Step 6: Configure Agent YAML to Use the Provider

Update your agent configuration in `config/agents/<agent>.yaml`:

```yaml
spec:
  model:
    provider: anthropic
    model: claude-3-5-sonnet-latest
    temperature: 0.2
```

---

## Troubleshooting

### `isinstance(adapter, ModelAdapter)` returns `False`

**Cause**: Missing `invoke` or `stream` method signature on the adapter class.

**Solution**: Ensure both `async def invoke(...)` and `def stream(...)` are defined on the class.

---

### Cost tracking reports `$0.00` or falls back to default pricing

**Cause**: Model identifier is not listed in `MODEL_PRICING`.

**Solution**: Add the exact model name returned in `ModelResponse.model` to `MODEL_PRICING` in `src/product_value_system/observability/cost_tracker.py`.

---

### Rate limit crashes run instead of retrying

**Cause**: Provider SDK rate-limit exception was not caught or translated.

**Solution**: Catch the provider's HTTP 429 error and either retry with exponential backoff or raise `RateLimitError(retry_after_seconds=...)`.

---

## Next Steps

- [Create a Custom Agent](create-custom-agent.md) using your new model adapter
- See [Harness API Reference](../reference/api/harness.md) for `Harness.invoke_model` details
- See [Environment Variables Reference](../reference/configuration/environment-variables.md)

---

**Last Updated**: 2026-11-18

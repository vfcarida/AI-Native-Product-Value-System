"""
Real integration tests against live Anthropic API (EVO-003).
"""

import os

import pytest

from product_value_system.adapters.anthropic_adapter import (
    AnthropicAdapter,
    ModelError,
)
from tests.integration_real.conftest import (
    RealIntegrationCostTracker,
    real_tests_enabled,
)


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled() or not os.getenv("ANTHROPIC_API_KEY"),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1 and ANTHROPIC_API_KEY",
)
class TestAnthropicRealIntegration:
    """Real integration tests with Anthropic Messages API."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invoke_claude_haiku(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test real invoke with claude-3-haiku (cheapest Claude model)."""
        adapter = AnthropicAdapter(model="claude-3-haiku-20240307")
        try:
            response = await adapter.invoke(
                messages=[{"role": "user", "content": "Say 'test' and nothing else"}],
                max_tokens=10,
                temperature=0.0,
            )

            assert isinstance(response.content, str)
            assert "test" in response.content.lower()
            assert response.usage is not None
            assert response.usage.prompt_tokens > 0
            assert response.usage.completion_tokens > 0
            assert response.usage.total_tokens == (
                response.usage.prompt_tokens + response.usage.completion_tokens
            )

            cost = adapter.calculate_cost(response.usage)
            cost_tracker.track(cost)
            assert 0 < cost < 0.01
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_system_message_real(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test top-level system prompt handling against live Anthropic API."""
        adapter = AnthropicAdapter(model="claude-3-haiku-20240307")
        try:
            response = await adapter.invoke(
                messages=[
                    {"role": "system", "content": "Respond only with the word READY."},
                    {"role": "user", "content": "Status check"},
                ],
                max_tokens=10,
                temperature=0.0,
            )

            assert "ready" in response.content.lower()
            assert response.usage is not None
            cost_tracker.track(adapter.calculate_cost(response.usage))
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_tool_calling_real(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test OpenAI-to-Anthropic tool calling translation against live API."""
        adapter = AnthropicAdapter(model="claude-3-haiku-20240307")
        try:
            response = await adapter.invoke(
                messages=[
                    {
                        "role": "user",
                        "content": "Call get_weather for San Francisco.",
                    }
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get current weather for a city",
                            "parameters": {
                                "type": "object",
                                "properties": {"location": {"type": "string"}},
                                "required": ["location"],
                            },
                        },
                    }
                ],
                max_tokens=100,
                temperature=0.0,
            )

            assert response.usage is not None
            cost_tracker.track(adapter.calculate_cost(response.usage))
            assert response.tool_calls is not None
            assert response.tool_calls[0]["name"] == "get_weather"
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_cost_tracking_accuracy(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test cost calculation stays accurate and within sub-cent bounds for Haiku."""
        adapter = AnthropicAdapter(model="claude-3-haiku-20240307")
        try:
            response = await adapter.invoke(
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=5,
                temperature=0.0,
            )

            assert response.usage is not None
            cost = adapter.calculate_cost(response.usage)
            cost_tracker.track(cost)
            assert 0 < cost < 0.001
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_error_handling_invalid_model(self) -> None:
        """Test invalid Claude model raises ModelError."""
        adapter = AnthropicAdapter(model="claude-nonexistent-model", max_retries=1)
        try:
            with pytest.raises(ModelError):
                await adapter.invoke(
                    messages=[{"role": "user", "content": "Test"}],
                    max_tokens=5,
                )
        finally:
            await adapter.close()

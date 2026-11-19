"""
Real integration tests against live OpenAI API (EVO-003).
"""

import os

import pytest

from product_value_system.adapters.model_adapter import ModelError, RateLimitError
from product_value_system.adapters.openai_adapter import OpenAIAdapter
from tests.integration_real.conftest import (
    RealIntegrationCostTracker,
    real_tests_enabled,
)


@pytest.mark.integration_real
@pytest.mark.skipif(
    not real_tests_enabled() or not os.getenv("OPENAI_API_KEY"),
    reason="Requires RUN_REAL_INTEGRATION_TESTS=1 and OPENAI_API_KEY",
)
class TestOpenAIRealIntegration:
    """Real integration tests with OpenAI API."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_invoke_gpt35_turbo(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test real invoke with gpt-3.5-turbo (cheapest model)."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            response = await adapter.invoke(
                messages=[{"role": "user", "content": "Say 'test' and nothing else"}],
                max_tokens=10,
                temperature=0,
            )

            assert isinstance(response.content, str)
            assert len(response.content) > 0
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
    @pytest.mark.timeout(60)
    async def test_rate_limit_recovery(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test that adapter handles rapid requests and rate limits gracefully."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo", max_retries=3)
        try:
            responses = []
            for i in range(5):
                try:
                    response = await adapter.invoke(
                        messages=[{"role": "user", "content": f"Count to {i}"}],
                        max_tokens=10,
                    )
                    if response.usage is not None:
                        cost_tracker.track(adapter.calculate_cost(response.usage))
                    responses.append(response)
                except RateLimitError:
                    break

            assert len(responses) > 0
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_tool_calling_real(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test real function calling with OpenAI."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            response = await adapter.invoke(
                messages=[
                    {"role": "user", "content": "What's the weather in San Francisco?"}
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get current weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"location": {"type": "string"}},
                                "required": ["location"],
                            },
                        },
                    }
                ],
                max_tokens=50,
            )

            if response.usage is not None:
                cost_tracker.track(adapter.calculate_cost(response.usage))

            assert response.tool_calls is not None
            assert len(response.tool_calls) > 0
            assert response.tool_calls[0]["name"] == "get_weather"
            assert "location" in str(response.tool_calls[0]["arguments"])
        finally:
            await adapter.close()

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)
    async def test_cost_tracking_accuracy(
        self, cost_tracker: RealIntegrationCostTracker
    ) -> None:
        """Test that cost calculation matches OpenAI pricing."""
        adapter = OpenAIAdapter(model="gpt-3.5-turbo")
        try:
            response = await adapter.invoke(
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=5,
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
        """Test error handling for invalid model name."""
        adapter = OpenAIAdapter(model="gpt-nonexistent", max_retries=1)
        try:
            with pytest.raises(ModelError):
                await adapter.invoke(
                    messages=[{"role": "user", "content": "Test"}],
                    max_tokens=5,
                )
        finally:
            await adapter.close()

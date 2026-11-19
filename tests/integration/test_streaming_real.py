"""
Real API streaming integration tests for OpenAI and Anthropic adapters (EVO-002).
Skipped automatically in CI when API keys are not set.
"""

import os

import pytest

from product_value_system.adapters.anthropic_adapter import AnthropicAdapter
from product_value_system.adapters.openai_adapter import OpenAIAdapter


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="No OPENAI_API_KEY")
@pytest.mark.asyncio
async def test_openai_real_streaming() -> None:
    """Test real OpenAI SSE streaming returns multiple incremental chunks."""
    adapter = OpenAIAdapter(model="gpt-3.5-turbo")

    chunks = [
        chunk
        async for chunk in adapter.stream(
            [{"role": "user", "content": "Count from 1 to 5"}],
            max_tokens=50,
            stream_options={"include_usage": True},
        )
    ]

    assert len(chunks) > 3
    assert chunks[-1].usage is not None
    await adapter.close()


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="No ANTHROPIC_API_KEY")
@pytest.mark.asyncio
async def test_anthropic_real_streaming() -> None:
    """Test real Anthropic SSE streaming returns multiple incremental chunks."""
    adapter = AnthropicAdapter(model="claude-3-5-sonnet-20241022")

    chunks = [
        chunk
        async for chunk in adapter.stream(
            [{"role": "user", "content": "Count from 1 to 5"}],
            max_tokens=50,
        )
    ]

    assert len(chunks) > 3
    assert chunks[-1].usage is not None
    await adapter.close()

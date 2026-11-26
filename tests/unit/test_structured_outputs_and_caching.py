"""
Tests for Native Structured Outputs (Pydantic schema + self-healing retries)
and Native Provider Prompt Caching across Anthropic, OpenAI, Gemini, Mock,
and Harness (EVO-P3-08).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from product_value_system.adapters.anthropic_adapter import AnthropicAdapter
from product_value_system.adapters.gemini_adapter import GeminiModelAdapter
from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    Message,
    Role,
    StructuredOutputError,
    Usage,
    extract_json_payload,
)
from product_value_system.adapters.openai_adapter import OpenAIModelAdapter
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.harness import Harness


class _RiskAssessment(BaseModel):
    risk_score: int = Field(ge=1, le=10)
    summary: str
    mitigations: list[str]


def test_extract_json_payload_handles_fences_and_prose() -> None:
    fenced = 'Here is the analysis:\n```json\n{"risk_score": 4, "summary": "Low"}\n```'
    assert extract_json_payload(fenced) == '{"risk_score": 4, "summary": "Low"}'

    prose = 'Result -> {"risk_score": 2, "summary": "OK"} done.'
    assert extract_json_payload(prose) == '{"risk_score": 2, "summary": "OK"}'


@pytest.mark.asyncio
async def test_harness_invoke_structured_self_heals_validation_errors() -> None:
    """Harness.invoke_structured retries with Pydantic error feedback until valid."""
    mock = MockModelAdapter(
        responses=[
            "not valid json at all",
            '{"risk_score": 99, "summary": "Out of range", "mitigations": []}',
            '```json\n{"risk_score": 7, "summary": "High coupling", "mitigations": ["ACL"]}\n```',
        ]
    )
    harness = Harness(
        context=ExecutionContext(run_id="run-struct-1"),
        model_adapter=mock,
    )

    parsed, resp = await harness.invoke_structured(
        messages=[Message(role=Role.USER, content="Assess migration risk")],
        response_schema=_RiskAssessment,
        max_validation_retries=2,
    )
    assert isinstance(parsed, _RiskAssessment)
    assert parsed.risk_score == 7
    assert parsed.mitigations == ["ACL"]
    assert mock._call_count == 3
    assert resp.content


@pytest.mark.asyncio
async def test_harness_invoke_structured_raises_after_exhausting_retries() -> None:
    mock = MockModelAdapter(responses=["{bad", "{still bad"])
    harness = Harness(
        context=ExecutionContext(run_id="run-struct-fail"),
        model_adapter=mock,
    )

    with pytest.raises(StructuredOutputError) as exc_info:
        await harness.invoke_structured(
            messages=[Message(role=Role.USER, content="Return risk JSON")],
            response_schema=_RiskAssessment,
            max_validation_retries=1,
        )
    assert exc_info.value.attempts == 2


@pytest.mark.asyncio
async def test_mock_adapter_simulates_native_prompt_cache_hits() -> None:
    mock = MockModelAdapter(responses=["first", "second"])
    system_prompt = Message(
        role=Role.SYSTEM,
        content="Long invariant architecture specification context for caching.",
        cache_control=True,
    )

    r1 = await mock.invoke([system_prompt, Message(role=Role.USER, content="Question 1")])
    r2 = await mock.invoke([system_prompt, Message(role=Role.USER, content="Question 2")])

    assert r1.usage is not None and r2.usage is not None
    assert r1.usage.cache_creation_tokens > 0
    assert r1.usage.cache_read_tokens == 0
    assert r2.usage.cache_read_tokens == r1.usage.cache_creation_tokens
    assert r2.usage.cache_hit_ratio > 0.5


def test_anthropic_native_prompt_caching_schema_and_cost_discount() -> None:
    adapter = AnthropicAdapter(api_key="sk-ant-test-unit-only")

    payload = adapter._build_payload(
        messages=[
            Message(role=Role.SYSTEM, content="Invariant system prompt", cache_control=True),
            Message(role=Role.USER, content="Evaluate risk"),
        ],
        response_schema=_RiskAssessment,
    )
    assert isinstance(payload["system"], list)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "risk_score" in payload["system"][1]["text"]

    parsed = adapter._parse_response(
        {
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100_000,
                "output_tokens": 10_000,
                "cache_read_input_tokens": 1_000_000,
                "cache_creation_input_tokens": 200_000,
            },
        }
    )
    assert parsed.usage is not None
    assert parsed.usage.cache_read_tokens == 1_000_000
    assert parsed.usage.cache_creation_tokens == 200_000

    # Sonnet pricing: input $3/M, output $15/M, cache read $0.30/M, cache create $3.75/M
    cost = adapter.calculate_cost(parsed.usage, "claude-3-5-sonnet-20241022")
    expected = (0.1 * 3.0) + (1.0 * 0.30) + (0.2 * 3.75) + (0.01 * 15.0)
    assert cost == pytest.approx(expected, rel=1e-6)


def test_openai_native_structured_output_and_cache_discount() -> None:
    adapter = OpenAIModelAdapter(api_key="sk-test-unit-only", model="gpt-4o")

    payload = adapter._build_payload(
        messages=[
            Message(role=Role.SYSTEM, content="Stable policy prefix", cache_control=True),
            Message(role=Role.USER, content="Evaluate"),
        ],
        tools=None,
        temperature=0.0,
        max_tokens=500,
        response_schema=_RiskAssessment,
    )
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["name"] == "_RiskAssessment"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["prompt_cache_key"].startswith("pvs-cache-")

    parsed = adapter._parse_response(
        {
            "model": "gpt-4o",
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1_000_000,
                "completion_tokens": 100_000,
                "total_tokens": 1_100_000,
                "prompt_tokens_details": {"cached_tokens": 800_000},
            },
        }
    )
    assert parsed.usage is not None
    assert parsed.usage.cache_read_tokens == 800_000
    # gpt-4o: $2.50/M input, 50% off cached ($1.25/M), $10/M output
    cost = adapter.calculate_cost(parsed.usage, "gpt-4o")
    expected = (0.2 * 2.50) + (0.8 * 1.25) + (0.1 * 10.0)
    assert cost == pytest.approx(expected, rel=1e-6)


def test_gemini_native_response_schema_cached_content_and_cost_discount() -> None:
    adapter = GeminiModelAdapter(api_key="test-gemini-unit-key", model="gemini-2.5-pro")

    payload = adapter._build_payload(
        messages=[Message(role=Role.USER, content="Evaluate risk")],
        tools=None,
        temperature=0.1,
        max_tokens=1000,
        response_schema=_RiskAssessment,
        cached_content="cachedContents/spec-context-v1",
    )
    gen_cfg = payload["generationConfig"]
    assert gen_cfg["responseMimeType"] == "application/json"
    assert "risk_score" in gen_cfg["responseSchema"]["properties"]
    assert "additionalProperties" not in gen_cfg["responseSchema"]
    assert payload["cachedContent"] == "cachedContents/spec-context-v1"

    parsed = adapter._parse_response(
        {
            "candidates": [
                {"content": {"parts": [{"text": "{}"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 1_000_000,
                "candidatesTokenCount": 100_000,
                "totalTokenCount": 1_100_000,
                "cachedContentTokenCount": 800_000,
            },
        },
        model_name="gemini-2.5-pro",
    )
    assert parsed.usage is not None
    assert parsed.usage.cache_read_tokens == 800_000
    # gemini-2.5-pro: $1.25/M input, 75% off cached ($0.3125/M), $10/M output
    cost = adapter.calculate_cost(parsed.usage, "gemini-2.5-pro")
    expected = (0.2 * 1.25) + (0.8 * 1.25 * 0.25) + (0.1 * 10.0)
    assert cost == pytest.approx(expected, rel=1e-6)

    # Zero cache tokens keeps legacy Usage equality intact
    assert Usage(10, 5) == Usage(input_tokens=10, output_tokens=5, total_tokens=15)

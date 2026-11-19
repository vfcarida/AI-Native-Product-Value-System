"""
Mock Server-Sent Events (SSE) response generator for streaming adapter tests.
"""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx


class _SSEAsyncByteStream(httpx.AsyncByteStream):
    """Async byte stream yielding SSE lines with simulated network delay."""

    def __init__(self, events: list[str], delay_seconds: float = 0.001) -> None:
        self._events = events
        self._delay_seconds = delay_seconds

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for event in self._events:
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            yield f"{event}\n".encode()

    async def aclose(self) -> None:
        """Close the async stream."""


class MockSSEResponse(httpx.Response):
    """Mock SSE httpx.Response for deterministic streaming tests."""

    def __init__(
        self,
        events: list[str],
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        delay_seconds: float = 0.001,
    ) -> None:
        """
        Args:
            events: List of SSE lines (including ``event:`` / ``data:`` prefixes)
            status_code: HTTP status code
            headers: Optional extra HTTP response headers
            delay_seconds: Per-line simulated delay
        """
        self.events = events
        response_headers = {"content-type": "text/event-stream"}
        if headers:
            response_headers.update(headers)
        super().__init__(
            status_code=status_code,
            headers=response_headers,
            stream=_SSEAsyncByteStream(events, delay_seconds=delay_seconds),
        )


def create_openai_sse_chunks(
    content: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
) -> list[str]:
    """
    Create OpenAI-format SSE events for testing.

    Args:
        content: Text content to split into word chunks
        finish_reason: Final finish reason (e.g., ``stop``, ``length``)
        prompt_tokens: Simulated prompt token count

    Returns:
        List of SSE event lines
    """
    events: list[str] = []

    words = content.split()
    for i, word in enumerate(words):
        chunk_content = word if i == 0 else f" {word}"
        chunk_obj = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk_content},
                    "finish_reason": None,
                }
            ],
        }
        events.append(f"data: {json.dumps(chunk_obj)}")

    final_obj = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": len(words),
            "total_tokens": prompt_tokens + len(words),
        },
    }
    events.append(f"data: {json.dumps(final_obj)}")
    events.append("data: [DONE]")
    return events


def create_anthropic_sse_chunks(
    content: str,
    finish_reason: str = "end_turn",
    input_tokens: int = 10,
) -> list[str]:
    """
    Create Anthropic-format SSE events for testing.

    Args:
        content: Text content to split into word chunks
        finish_reason: Final Anthropic stop_reason (e.g., ``end_turn``, ``max_tokens``)
        input_tokens: Simulated input token count

    Returns:
        List of SSE event lines
    """
    events: list[str] = []

    events.append("event: message_start")
    events.append(
        "data: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "role": "assistant",
                    "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                },
            }
        )
    )

    events.append("event: content_block_start")
    events.append(
        "data: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )
    )

    words = content.split()
    for i, word in enumerate(words):
        chunk_content = word if i == 0 else f" {word}"
        events.append("event: content_block_delta")
        events.append(
            "data: "
            + json.dumps(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": chunk_content},
                }
            )
        )

    events.append("event: content_block_stop")
    events.append('data: {"type":"content_block_stop","index":0}')

    events.append("event: message_delta")
    events.append(
        "data: "
        + json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": finish_reason},
                "usage": {"output_tokens": len(words)},
            }
        )
    )

    events.append("event: message_stop")
    events.append('data: {"type":"message_stop"}')
    return events

"""
Batch API adapters for OpenAI Batch API (50% discount) and
Anthropic Message Batches API (30% discount) (EVO-027).
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from product_value_system.security.secret_detection import SecretDetector

logger = logging.getLogger(__name__)


@dataclass
class BatchJob:
    """Provider batch evaluation job metadata and audit state."""

    batch_id: str
    provider: str  # 'openai' or 'anthropic'
    num_requests: int
    status: str  # 'pending', 'in_progress', 'completed', 'failed', 'expired', 'cancelled'
    created_at: str
    completed_at: str | None = None
    result_file_id: str | None = None
    error: str | None = None
    cost_usd: float = 0.0
    realtime_equivalent_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "provider": self.provider,
            "num_requests": self.num_requests,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result_file_id": self.result_file_id,
            "error": self.error,
            "cost_usd": round(self.cost_usd, 6),
            "realtime_equivalent_cost_usd": round(self.realtime_equivalent_cost_usd, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class BatchResult:
    """Parsed single-sample response from a completed batch evaluation job."""

    custom_id: str
    response: dict[str, Any]
    error: str | None = None
    content: str = ""
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "custom_id": self.custom_id,
            "content": self.content,
            "response": self.response,
            "error": self.error,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class BatchAdapter(ABC):
    """Abstract provider interface for asynchronous LLM batch APIs."""

    discount_rate: float = 0.0
    max_requests_per_batch: int = 10000

    @abstractmethod
    async def submit_batch(self, batch_file: Path) -> BatchJob:
        """Upload or submit a JSONL batch file and return a ``BatchJob``."""

    @abstractmethod
    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        """Fetch normalized batch status dictionary (``status``, ``output_file_id``, etc.)."""

    @abstractmethod
    async def download_result_file(self, file_id: str) -> str:
        """Download raw JSONL result content for a completed batch."""

    @abstractmethod
    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        """Request cancellation of an in-flight batch job."""


class OpenAIBatchAdapter(BatchAdapter):
    """OpenAI Batch API adapter (50% discount, up to 50,000 requests/batch, 24h SLA)."""

    discount_rate = 0.50
    max_requests_per_batch = 50000

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required for OpenAIBatchAdapter.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self._transport = transport
        self._injected_client = client
        self._secret_detector = SecretDetector()

    def _make_client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "headers": {"Authorization": f"Bearer {self.api_key}"},
            "timeout": httpx.Timeout(self.timeout),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        owns_client = self._injected_client is None
        client = self._make_client()
        try:
            for attempt in range(self.max_retries):
                try:
                    resp = await client.request(method, url, **kwargs)
                    if resp.status_code in {429, 500, 502, 503, 504} and attempt < (
                        self.max_retries - 1
                    ):
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    if resp.status_code >= 400:
                        safe_body = self._secret_detector.redact_secrets(resp.text)
                        raise RuntimeError(
                            f"OpenAI Batch API error ({resp.status_code}): {safe_body}"
                        )
                    return resp
                except httpx.RequestError as exc:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    raise RuntimeError(f"OpenAI Batch API network error: {exc}") from exc
            raise RuntimeError("OpenAI Batch API retry limit exhausted.")
        finally:
            if owns_client:
                await client.aclose()

    async def submit_batch(self, batch_file: Path) -> BatchJob:
        """Upload JSONL file to ``/files`` and create a 24h batch job via ``/batches``."""
        raw_bytes = batch_file.read_bytes()
        num_lines = sum(1 for line in raw_bytes.splitlines() if line.strip())

        upload_resp = await self._request_with_retry(
            "POST",
            "/files",
            files={"file": (batch_file.name, raw_bytes, "application/jsonl")},
            data={"purpose": "batch"},
        )
        file_id = str(upload_resp.json()["id"])

        batch_resp = await self._request_with_retry(
            "POST",
            "/batches",
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
        )
        data = batch_resp.json()
        counts = data.get("request_counts") or {}
        created_raw = data.get("created_at")
        created_iso = (
            datetime.fromtimestamp(int(created_raw), tz=UTC).isoformat()
            if isinstance(created_raw, (int, float))
            else str(created_raw or datetime.now(UTC).isoformat())
        )
        return BatchJob(
            batch_id=str(data["id"]),
            provider="openai",
            num_requests=int(counts.get("total", num_lines)),
            status=str(data.get("status", "pending")),
            created_at=created_iso,
            result_file_id=data.get("output_file_id"),
        )

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry("GET", f"/batches/{batch_id}")
        data: dict[str, Any] = resp.json()
        completed_raw = data.get("completed_at")
        completed_iso = (
            datetime.fromtimestamp(int(completed_raw), tz=UTC).isoformat()
            if isinstance(completed_raw, (int, float))
            else completed_raw
        )
        return {
            "batch_id": data.get("id", batch_id),
            "status": str(data.get("status", "in_progress")),
            "output_file_id": data.get("output_file_id"),
            "error_file_id": data.get("error_file_id"),
            "completed_at": completed_iso,
            "request_counts": data.get("request_counts", {}),
            "error": data.get("errors"),
        }

    async def download_result_file(self, file_id: str) -> str:
        resp = await self._request_with_retry("GET", f"/files/{file_id}/content")
        return resp.text

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry("POST", f"/batches/{batch_id}/cancel")
        return dict(resp.json())


class AnthropicBatchAdapter(BatchAdapter):
    """Anthropic Message Batches API adapter (30% discount, up to 10,000 requests/batch)."""

    discount_rate = 0.30
    max_requests_per_batch = 10000

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Anthropic API key is required for AnthropicBatchAdapter.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self._transport = transport
        self._injected_client = client
        self._secret_detector = SecretDetector()

    def _make_client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "headers": {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(self.timeout),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        owns_client = self._injected_client is None
        client = self._make_client()
        try:
            for attempt in range(self.max_retries):
                try:
                    resp = await client.request(method, url, **kwargs)
                    if resp.status_code in {429, 500, 502, 503, 504} and attempt < (
                        self.max_retries - 1
                    ):
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    if resp.status_code >= 400:
                        safe_body = self._secret_detector.redact_secrets(resp.text)
                        raise RuntimeError(
                            f"Anthropic Batch API error ({resp.status_code}): {safe_body}"
                        )
                    return resp
                except httpx.RequestError as exc:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(min(2**attempt, 8))
                        continue
                    raise RuntimeError(f"Anthropic Batch API network error: {exc}") from exc
            raise RuntimeError("Anthropic Batch API retry limit exhausted.")
        finally:
            if owns_client:
                await client.aclose()

    async def submit_batch(self, batch_file: Path) -> BatchJob:
        """Convert JSONL requests into Anthropic ``/messages/batches`` payload and submit."""
        requests_payload: list[dict[str, Any]] = []
        for raw_line in batch_file.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            item = json.loads(raw_line)
            if "params" in item:
                requests_payload.append(
                    {"custom_id": item["custom_id"], "params": item["params"]}
                )
            else:
                body = item.get("body", {})
                messages = [m for m in body.get("messages", []) if m.get("role") != "system"]
                system_parts = [
                    m.get("content", "")
                    for m in body.get("messages", [])
                    if m.get("role") == "system"
                ]
                params: dict[str, Any] = {
                    "model": body.get("model", "claude-3-5-sonnet-20241022"),
                    "max_tokens": int(body.get("max_tokens", 1024)),
                    "messages": messages,
                    "temperature": float(body.get("temperature", 0.0)),
                }
                if system_parts:
                    params["system"] = "\n".join(system_parts)
                requests_payload.append({"custom_id": item["custom_id"], "params": params})

        resp = await self._request_with_retry(
            "POST",
            "/messages/batches",
            json={"requests": requests_payload},
        )
        data = resp.json()
        raw_status = str(data.get("processing_status", "in_progress"))
        normalized_status = "completed" if raw_status == "ended" else raw_status
        return BatchJob(
            batch_id=str(data["id"]),
            provider="anthropic",
            num_requests=len(requests_payload),
            status=normalized_status,
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
            result_file_id=data.get("results_url"),
        )

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry("GET", f"/messages/batches/{batch_id}")
        data = resp.json()
        raw_status = str(data.get("processing_status", "in_progress"))
        normalized = "completed" if raw_status == "ended" else raw_status
        return {
            "batch_id": data.get("id", batch_id),
            "status": normalized,
            "output_file_id": data.get("results_url") or f"/messages/batches/{batch_id}/results",
            "completed_at": data.get("ended_at"),
            "request_counts": data.get("request_counts", {}),
            "error": data.get("error"),
        }

    async def download_result_file(self, file_id: str) -> str:
        url = file_id
        if url.startswith(self.base_url):
            url = url[len(self.base_url) :]
        resp = await self._request_with_retry("GET", url)
        return resp.text

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry("POST", f"/messages/batches/{batch_id}/cancel")
        return dict(resp.json())

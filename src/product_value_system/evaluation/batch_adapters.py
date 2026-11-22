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


class GeminiBatchAdapter(BatchAdapter):
    """Google Gemini Batch API adapter (50% discount, up to 50,000 requests/batch, 24h SLA)."""

    discount_rate = 0.50
    max_requests_per_batch = 50000

    _STATE_MAP: dict[str, str] = {
        "BATCH_STATE_PENDING": "pending",
        "BATCH_STATE_RUNNING": "in_progress",
        "BATCH_STATE_SUCCEEDED": "completed",
        "BATCH_STATE_FAILED": "failed",
        "BATCH_STATE_CANCELLED": "cancelled",
        "BATCH_STATE_EXPIRED": "expired",
    }

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("Gemini API key is required for GeminiBatchAdapter.")
        self.api_key = api_key
        self.model = model
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
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(self.timeout),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        client = self._make_client()
        owns_client = self._injected_client is None
        try:
            for attempt in range(self.max_retries):
                try:
                    resp = await client.request(method, url, **kwargs)
                    if resp.status_code == 429 and attempt < self.max_retries - 1:
                        await asyncio.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
                        continue
                    if resp.status_code >= 500 and attempt < self.max_retries - 1:
                        await asyncio.sleep(float(2**attempt))
                        continue
                    if resp.status_code >= 400:
                        safe_err = self._secret_detector.redact_secrets(resp.text)
                        raise RuntimeError(f"Gemini Batch API error {resp.status_code}: {safe_err}")
                    return resp
                except httpx.RequestError as exc:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(float(2**attempt))
                        continue
                    raise RuntimeError(f"Gemini Batch API network error: {exc}") from exc
            raise RuntimeError("Gemini Batch API retry limit exhausted.")
        finally:
            if owns_client:
                await client.aclose()

    async def submit_batch(self, batch_file: Path) -> BatchJob:
        """Submit JSONL judge requests to Gemini ``:batchGenerateContent``."""
        inlined_requests: list[dict[str, Any]] = []
        resolved_model = self.model
        for raw_line in batch_file.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            item = json.loads(raw_line)
            body = item.get("body", {})
            if body.get("model"):
                resolved_model = str(body["model"])
            contents: list[dict[str, Any]] = []
            system_parts: list[str] = []
            for msg in body.get("messages", []):
                role = str(msg.get("role", "user"))
                text = str(msg.get("content", ""))
                if role == "system":
                    system_parts.append(text)
                else:
                    gemini_role = "model" if role == "assistant" else "user"
                    contents.append({"role": gemini_role, "parts": [{"text": text}]})
            req_obj: dict[str, Any] = {
                "contents": contents,
                "generationConfig": {"temperature": float(body.get("temperature", 0.0))},
            }
            if system_parts:
                req_obj["systemInstruction"] = {"parts": [{"text": "\n".join(system_parts)}]}
            inlined_requests.append(
                {
                    "request": req_obj,
                    "metadata": {"key": str(item["custom_id"])},
                }
            )

        payload = {
            "batch": {
                "displayName": batch_file.stem,
                "inputConfig": {"requests": {"requests": inlined_requests}},
            }
        }
        resp = await self._request_with_retry(
            "POST", f"/models/{resolved_model}:batchGenerateContent", json=payload
        )
        data = resp.json()
        raw_state = str(
            (data.get("metadata") or {}).get("state") or data.get("state") or "BATCH_STATE_PENDING"
        )
        normalized = self._STATE_MAP.get(raw_state, "in_progress")
        batch_id = str(data.get("name") or f"batches/{batch_file.stem}")
        output_ref = (
            (data.get("response") or {}).get("responsesFile")
            or (data.get("metadata") or {}).get("output", {}).get("responsesFile")
            or f"/{batch_id}:results"
        )
        return BatchJob(
            batch_id=batch_id,
            provider="gemini",
            num_requests=len(inlined_requests),
            status=normalized,
            created_at=str(data.get("createTime") or datetime.now(UTC).isoformat()),
            result_file_id=str(output_ref) if normalized == "completed" else None,
        )

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        path = batch_id if batch_id.startswith("/") else f"/{batch_id}"
        resp = await self._request_with_retry("GET", path)
        data = resp.json()
        raw_state = str(
            (data.get("metadata") or {}).get("state") or data.get("state") or "BATCH_STATE_RUNNING"
        )
        normalized = self._STATE_MAP.get(raw_state, "in_progress")
        if data.get("done") is True and normalized not in {"failed", "cancelled", "expired"}:
            normalized = "completed"
        output_file = (
            (data.get("response") or {}).get("responsesFile")
            or (data.get("metadata") or {}).get("output", {}).get("responsesFile")
            or f"{path}:results"
        )
        return {
            "batch_id": data.get("name", batch_id),
            "status": normalized,
            "output_file_id": output_file,
            "completed_at": data.get("updateTime"),
            "error": data.get("error"),
        }

    async def download_result_file(self, file_id: str) -> str:
        path = file_id if file_id.startswith("/") else f"/{file_id}"
        resp = await self._request_with_retry("GET", path)
        return resp.text

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        path = batch_id if batch_id.startswith("/") else f"/{batch_id}"
        resp = await self._request_with_retry("POST", f"{path}:cancel")
        return dict(resp.json()) if resp.text.strip() else {"status": "cancelled"}


class AzureOpenAIBatchAdapter(BatchAdapter):
    """Azure OpenAI Global Batch adapter (50% discount, ``/openai/files`` + ``/openai/batches``)."""

    discount_rate = 0.50
    max_requests_per_batch = 50000

    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        *,
        azure_ad_token: str | None = None,
        api_version: str = "2024-10-21",
        timeout: float = 60.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not endpoint and client is None:
            raise ValueError("Azure OpenAI endpoint is required for AzureOpenAIBatchAdapter.")
        if not api_key and not azure_ad_token and client is None:
            raise ValueError("Azure OpenAI api_key or azure_ad_token is required.")
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.azure_ad_token = azure_ad_token
        self.api_version = api_version
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self._transport = transport
        self._injected_client = client
        self._secret_detector = SecretDetector()

    def _make_client(self) -> httpx.AsyncClient:
        if self._injected_client is not None:
            return self._injected_client
        headers: dict[str, str] = {}
        if self.azure_ad_token:
            headers["Authorization"] = f"Bearer {self.azure_ad_token}"
        else:
            headers["api-key"] = self.api_key
        kwargs: dict[str, Any] = {
            "base_url": self.endpoint,
            "headers": headers,
            "timeout": httpx.Timeout(self.timeout),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _request_with_retry(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        client = self._make_client()
        owns_client = self._injected_client is None
        params = dict(kwargs.pop("params", None) or {})
        params.setdefault("api-version", self.api_version)
        try:
            for attempt in range(self.max_retries):
                try:
                    resp = await client.request(method, url, params=params, **kwargs)
                    if resp.status_code == 429 and attempt < self.max_retries - 1:
                        await asyncio.sleep(float(resp.headers.get("Retry-After", 2**attempt)))
                        continue
                    if resp.status_code >= 500 and attempt < self.max_retries - 1:
                        await asyncio.sleep(float(2**attempt))
                        continue
                    if resp.status_code >= 400:
                        safe_err = self._secret_detector.redact_secrets(resp.text)
                        raise RuntimeError(
                            f"Azure OpenAI Batch API error {resp.status_code}: {safe_err}"
                        )
                    return resp
                except httpx.RequestError as exc:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(float(2**attempt))
                        continue
                    raise RuntimeError(f"Azure OpenAI Batch API network error: {exc}") from exc
            raise RuntimeError("Azure OpenAI Batch API retry limit exhausted.")
        finally:
            if owns_client:
                await client.aclose()

    async def submit_batch(self, batch_file: Path) -> BatchJob:
        raw_bytes = batch_file.read_bytes()
        num_requests = sum(
            1 for line in raw_bytes.decode("utf-8").splitlines() if line.strip()
        )
        upload_resp = await self._request_with_retry(
            "POST",
            "/openai/files",
            data={"purpose": "batch"},
            files={"file": (batch_file.name, raw_bytes, "application/jsonl")},
        )
        file_id = str(upload_resp.json()["id"])

        batch_resp = await self._request_with_retry(
            "POST",
            "/openai/batches",
            json={
                "input_file_id": file_id,
                "endpoint": "/chat/completions",
                "completion_window": "24h",
            },
        )
        data = batch_resp.json()
        return BatchJob(
            batch_id=str(data["id"]),
            provider="azure_openai",
            num_requests=num_requests,
            status=str(data.get("status", "in_progress")),
            created_at=datetime.now(UTC).isoformat(),
            result_file_id=data.get("output_file_id"),
        )

    async def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry("GET", f"/openai/batches/{batch_id}")
        data = resp.json()
        return {
            "batch_id": data.get("id", batch_id),
            "status": data.get("status", "in_progress"),
            "output_file_id": data.get("output_file_id"),
            "completed_at": data.get("completed_at"),
            "error": data.get("errors"),
        }

    async def download_result_file(self, file_id: str) -> str:
        resp = await self._request_with_retry("GET", f"/openai/files/{file_id}/content")
        return resp.text

    async def cancel_batch(self, batch_id: str) -> dict[str, Any]:
        resp = await self._request_with_retry("POST", f"/openai/batches/{batch_id}/cancel")
        return dict(resp.json())

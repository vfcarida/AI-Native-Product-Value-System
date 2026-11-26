"""
Bidirectional Work Tracking Integration (Jira, Linear, GitHub Issues) for
synchronizing Kaizen backlog and blameless postmortem action items (EVO-P3-17).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import httpx

from product_value_system.secrets.vault_client import resolve_secret


@dataclass
class WorkItem:
    """Canonical Kaizen or postmortem follow-up item synchronized with external trackers."""

    local_id: str
    title: str
    description: str
    status: str = "open"  # open, in_progress, done
    priority: str = "P2"
    source_type: str = "kaizen"  # kaizen, postmortem-action
    labels: list[str] = field(default_factory=list)
    external_id: str | None = None
    external_url: str | None = None
    provider: str = ""
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "local_id": self.local_id,
            "external_id": self.external_id,
            "external_url": self.external_url,
            "provider": self.provider,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "source_type": self.source_type,
            "labels": list(self.labels),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class SyncSummary:
    """Outcome summary for a bidirectional work-tracking sync pass."""

    provider: str
    created: int
    updated_remote: int
    updated_local: int
    errors: list[str] = field(default_factory=list)


@runtime_checkable
class WorkTrackerClient(Protocol):
    """Protocol for Jira, Linear, and GitHub Issues connectors."""

    @property
    def provider_name(self) -> str: ...

    async def create_issue(self, item: WorkItem) -> tuple[str, str]: ...

    async def update_issue(self, item: WorkItem) -> None: ...

    async def get_issue_status(self, external_id: str) -> str | None: ...

    async def close(self) -> None: ...


class JiraWorkTracker:
    """Jira Cloud REST API v3 client (supports Vault secret resolution & custom transport)."""

    _STATUS_MAP = {
        "to do": "open",
        "open": "open",
        "in progress": "in_progress",
        "done": "done",
        "closed": "done",
    }

    def __init__(
        self,
        *,
        base_url: str,
        project_key: str,
        email: str,
        api_token: str | None = None,
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved = resolve_secret(
            vault_path="product-value-system/jira",
            key="api_token",
            env_vars=("JIRA_API_TOKEN",),
            explicit=api_token,
        )
        if not resolved:
            raise ValueError("Jira API token required")
        self.base_url = base_url.rstrip("/")
        self.project_key = project_key
        basic = base64.b64encode(f"{email}:{resolved}".encode()).decode("ascii")
        client_kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "headers": {
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    @property
    def provider_name(self) -> str:
        return "jira"

    async def create_issue(self, item: WorkItem) -> tuple[str, str]:
        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": item.title,
                "description": item.description,
                "issuetype": {"name": "Task"},
                "labels": list({item.source_type, *item.labels}),
            }
        }
        resp = await self._client.post("/rest/api/3/issue", json=payload)
        resp.raise_for_status()
        data = resp.json()
        key = str(data.get("key") or data.get("id"))
        return key, f"{self.base_url}/browse/{key}"

    async def update_issue(self, item: WorkItem) -> None:
        if not item.external_id:
            raise ValueError("Cannot update Jira issue without external_id")
        payload = {
            "fields": {
                "summary": item.title,
                "description": item.description,
                "labels": list({item.source_type, *item.labels}),
            }
        }
        resp = await self._client.put(f"/rest/api/3/issue/{item.external_id}", json=payload)
        resp.raise_for_status()

    async def get_issue_status(self, external_id: str) -> str | None:
        resp = await self._client.get(
            f"/rest/api/3/issue/{external_id}",
            params={"fields": "status"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        raw_status = str(
            ((resp.json().get("fields") or {}).get("status") or {}).get("name", "")
        ).lower()
        return self._STATUS_MAP.get(raw_status, "open")

    async def close(self) -> None:
        await self._client.aclose()


class LinearWorkTracker:
    """Linear GraphQL API work tracker client."""

    _STATE_TYPE_MAP = {
        "backlog": "open",
        "unstarted": "open",
        "started": "in_progress",
        "completed": "done",
        "canceled": "done",
    }

    def __init__(
        self,
        *,
        team_id: str,
        api_key: str | None = None,
        endpoint: str = "https://api.linear.app/graphql",
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved = resolve_secret(
            vault_path="product-value-system/linear",
            key="api_key",
            env_vars=("LINEAR_API_KEY",),
            explicit=api_key,
        )
        if not resolved:
            raise ValueError("Linear API key required")
        self.team_id = team_id
        self.endpoint = endpoint
        client_kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": resolved,
                "Content-Type": "application/json",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    @property
    def provider_name(self) -> str:
        return "linear"

    async def create_issue(self, item: WorkItem) -> tuple[str, str]:
        mutation = (
            "mutation($teamId: String!, $title: String!, $description: String) { "
            "issueCreate(input: { teamId: $teamId, title: $title, description: $description }) "
            "{ success issue { id identifier url } } }"
        )
        resp = await self._client.post(
            self.endpoint,
            json={
                "query": mutation,
                "variables": {
                    "teamId": self.team_id,
                    "title": item.title,
                    "description": item.description,
                },
            },
        )
        resp.raise_for_status()
        issue = ((resp.json().get("data") or {}).get("issueCreate") or {}).get("issue") or {}
        return str(issue.get("identifier") or issue.get("id")), str(issue.get("url", ""))

    async def update_issue(self, item: WorkItem) -> None:
        if not item.external_id:
            raise ValueError("Cannot update Linear issue without external_id")
        mutation = (
            "mutation($id: String!, $title: String!, $description: String) { "
            "issueUpdate(id: $id, input: { title: $title, description: $description }) "
            "{ success } }"
        )
        resp = await self._client.post(
            self.endpoint,
            json={
                "query": mutation,
                "variables": {
                    "id": item.external_id,
                    "title": item.title,
                    "description": item.description,
                },
            },
        )
        resp.raise_for_status()

    async def get_issue_status(self, external_id: str) -> str | None:
        query = "query($id: String!) { issue(id: $id) { state { type } } }"
        resp = await self._client.post(
            self.endpoint,
            json={"query": query, "variables": {"id": external_id}},
        )
        resp.raise_for_status()
        issue = (resp.json().get("data") or {}).get("issue")
        if not issue:
            return None
        state_type = str((issue.get("state") or {}).get("type", "")).lower()
        return self._STATE_TYPE_MAP.get(state_type, "open")

    async def close(self) -> None:
        await self._client.aclose()


class GitHubIssuesWorkTracker:
    """GitHub Issues REST API client for Kaizen & postmortem follow-up sync."""

    def __init__(
        self,
        *,
        repo: str,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        resolved = resolve_secret(
            vault_path="product-value-system/github",
            key="token",
            env_vars=("GITHUB_TOKEN",),
            explicit=token,
        )
        if not resolved:
            raise ValueError("GitHub token required")
        self.repo = repo
        client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "headers": {
                "Authorization": f"Bearer {resolved}",
                "Accept": "application/vnd.github+json",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    @property
    def provider_name(self) -> str:
        return "github"

    async def create_issue(self, item: WorkItem) -> tuple[str, str]:
        resp = await self._client.post(
            f"/repos/{self.repo}/issues",
            json={
                "title": item.title,
                "body": item.description,
                "labels": list({item.source_type, *item.labels}),
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data["number"]), str(data.get("html_url", ""))

    async def update_issue(self, item: WorkItem) -> None:
        if not item.external_id:
            raise ValueError("Cannot update GitHub issue without external_id")
        gh_state = "closed" if item.status == "done" else "open"
        resp = await self._client.patch(
            f"/repos/{self.repo}/issues/{item.external_id}",
            json={"title": item.title, "body": item.description, "state": gh_state},
        )
        resp.raise_for_status()

    async def get_issue_status(self, external_id: str) -> str | None:
        resp = await self._client.get(f"/repos/{self.repo}/issues/{external_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        state = str(resp.json().get("state", "open")).lower()
        return "done" if state == "closed" else "open"

    async def close(self) -> None:
        await self._client.aclose()


class BidirectionalWorkSync:
    """Synchronizes local Kaizen/postmortem work items bidirectionally with Jira/Linear/GitHub."""

    def __init__(self, tracker: WorkTrackerClient) -> None:
        self.tracker = tracker

    async def sync(self, items: list[WorkItem]) -> SyncSummary:
        created = 0
        updated_remote = 0
        updated_local = 0
        errors: list[str] = []

        for item in items:
            try:
                if not item.external_id:
                    ext_id, ext_url = await self.tracker.create_issue(item)
                    item.external_id = ext_id
                    item.external_url = ext_url
                    item.provider = self.tracker.provider_name
                    item.updated_at = datetime.now(UTC)
                    created += 1
                else:
                    remote_status = await self.tracker.get_issue_status(item.external_id)
                    if remote_status and remote_status != item.status:
                        item.status = remote_status
                        item.updated_at = datetime.now(UTC)
                        updated_local += 1
                    else:
                        await self.tracker.update_issue(item)
                        updated_remote += 1
            except Exception as exc:
                errors.append(f"{item.local_id}: {exc}")

        return SyncSummary(
            provider=self.tracker.provider_name,
            created=created,
            updated_remote=updated_remote,
            updated_local=updated_local,
            errors=errors,
        )

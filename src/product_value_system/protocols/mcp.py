"""
Model Context Protocol (MCP) JSON-RPC 2.0 Server & Client governed by Cedar
``PolicyEngine`` and OWASP ASI01 indirect prompt injection protection (EVO-P3-07).

Server:
- Exposes registered PVS ``ToolRegistry`` tools, resources, and prompts over
  JSON-RPC 2.0 (HTTP ``/api/v1/mcp`` and ``stdio`` transport).
- Every ``tools/call`` executes through ``ToolRegistry.execute()``, enforcing
  Cedar authorization and OWASP ASI01 output validation.

Client:
- Connects to either a remote HTTP MCP server or an in-process ``MCPServer``.
- Discovers tools via ``tools/list`` and wraps each tool as an ``MCPToolProxy``
  compatible with ``ToolRegistry`` so remote tool calls are Cedar-gated and
  OWASP ASI01-scanned before returning to agents.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TextIO

import httpx
import jsonschema

from product_value_system.__version__ import __version__
from product_value_system.compliance.audit_logger import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
)
from product_value_system.harness.tools import ToolRegistry

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"

# Standard JSON-RPC 2.0 error codes
ERR_PARSE_ERROR = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL_ERROR = -32603
ERR_FORBIDDEN = -32001


@dataclass(frozen=True)
class MCPResource:
    """Read-only MCP resource exposed via ``resources/list`` and ``resources/read``."""

    uri: str
    name: str
    description: str
    mime_type: str = "application/json"
    reader: Callable[[], str | Awaitable[str]] = field(default=lambda: "{}")

    def to_metadata(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass(frozen=True)
class MCPPrompt:
    """Reusable MCP prompt template exposed via ``prompts/list`` and ``prompts/get``."""

    name: str
    description: str
    arguments: list[dict[str, Any]] = field(default_factory=list)
    renderer: Callable[[dict[str, Any]], str] = field(default=lambda _args: "")

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": self.arguments,
        }


def _jsonrpc_ok(req_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_err(
    req_id: Any,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class MCPServer:
    """
    Cedar-governed MCP JSON-RPC 2.0 server wrapping a PVS ``ToolRegistry``.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        *,
        name: str = "pvs-mcp-server",
        version: str = __version__,
        audit_logger: AuditLogger | None = None,
        default_environment: str = "sandbox",
    ) -> None:
        self.tool_registry = tool_registry
        self.name = name
        self.version = version
        self.audit_logger = audit_logger
        self.default_environment = default_environment
        self._resources: dict[str, MCPResource] = {}
        self._prompts: dict[str, MCPPrompt] = {}

    def register_resource(self, resource: MCPResource) -> None:
        self._resources[resource.uri] = resource

    def register_prompt(self, prompt: MCPPrompt) -> None:
        self._prompts[prompt.name] = prompt

    async def handle_jsonrpc(
        self,
        request_payload: dict[str, Any],
        *,
        agent_name: str = "discovery",
        tenant_id: str = "default",
        user_id: str = "mcp-caller",
        extra_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch a single JSON-RPC 2.0 MCP request."""
        if not isinstance(request_payload, dict) or request_payload.get("jsonrpc") != "2.0":
            return _jsonrpc_err(None, ERR_INVALID_REQUEST, "Invalid JSON-RPC 2.0 request")

        req_id = request_payload.get("id")
        method = request_payload.get("method")
        params = request_payload.get("params") or {}
        if not isinstance(method, str) or not isinstance(params, dict):
            return _jsonrpc_err(req_id, ERR_INVALID_REQUEST, "Missing method or invalid params")

        if method == "initialize":
            return _jsonrpc_ok(
                req_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {"name": self.name, "version": self.version},
                },
            )

        if method == "ping":
            return _jsonrpc_ok(req_id, {})

        if method == "tools/list":
            tools_out = []
            for tool_name in self.tool_registry.list_tools():
                tool = self.tool_registry.get_tool(tool_name)
                if tool is not None:
                    tools_out.append(
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "inputSchema": tool.parameters,
                        }
                    )
            return _jsonrpc_ok(req_id, {"tools": tools_out})

        if method == "tools/call":
            return await self._handle_tool_call(
                req_id=req_id,
                params=params,
                agent_name=agent_name,
                tenant_id=tenant_id,
                user_id=user_id,
                extra_context=extra_context,
            )

        if method == "resources/list":
            return _jsonrpc_ok(
                req_id,
                {"resources": [res.to_metadata() for res in self._resources.values()]},
            )

        if method == "resources/read":
            uri = str(params.get("uri") or "")
            resource = self._resources.get(uri)
            if resource is None:
                return _jsonrpc_err(req_id, ERR_INVALID_PARAMS, f"Unknown resource URI: {uri}")
            raw_content = resource.reader()
            content = await raw_content if inspect.isawaitable(raw_content) else str(raw_content)
            return _jsonrpc_ok(
                req_id,
                {
                    "contents": [
                        {
                            "uri": resource.uri,
                            "mimeType": resource.mime_type,
                            "text": str(content),
                        }
                    ]
                },
            )

        if method == "prompts/list":
            return _jsonrpc_ok(
                req_id,
                {"prompts": [p.to_metadata() for p in self._prompts.values()]},
            )

        if method == "prompts/get":
            prompt_name = str(params.get("name") or "")
            prompt = self._prompts.get(prompt_name)
            if prompt is None:
                return _jsonrpc_err(req_id, ERR_INVALID_PARAMS, f"Unknown prompt: {prompt_name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _jsonrpc_err(
                    req_id,
                    ERR_INVALID_PARAMS,
                    "Prompt arguments must be an object",
                )
            rendered = prompt.renderer(arguments)
            return _jsonrpc_ok(
                req_id,
                {
                    "description": prompt.description,
                    "messages": [
                        {
                            "role": "user",
                            "content": {"type": "text", "text": rendered},
                        }
                    ],
                },
            )

        return _jsonrpc_err(req_id, ERR_METHOD_NOT_FOUND, f"Method not found: {method}")

    async def _handle_tool_call(
        self,
        *,
        req_id: Any,
        params: dict[str, Any],
        agent_name: str,
        tenant_id: str,
        user_id: str,
        extra_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        tool_name = str(params.get("name") or "").strip()
        arguments = params.get("arguments") or {}
        if not tool_name or not isinstance(arguments, dict):
            return _jsonrpc_err(
                req_id,
                ERR_INVALID_PARAMS,
                "tools/call requires string 'name' and object 'arguments'",
            )

        tool = self.tool_registry.get_tool(tool_name)
        if tool is None:
            return _jsonrpc_err(req_id, ERR_INVALID_PARAMS, f"Tool '{tool_name}' not found")

        if isinstance(tool.parameters, dict) and tool.parameters:
            try:
                jsonschema.validate(instance=arguments, schema=tool.parameters)
            except jsonschema.ValidationError as exc:
                return _jsonrpc_err(
                    req_id,
                    ERR_INVALID_PARAMS,
                    f"Invalid tool arguments for '{tool_name}': {exc.message}",
                    data={"tool": tool_name, "path": [str(p) for p in exc.path]},
                )

        exec_context: dict[str, Any] = {
            "environment": self.default_environment,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "protocol": "mcp",
        }
        if extra_context:
            exec_context.update(extra_context)

        call_record = await self.tool_registry.execute(
            agent_name=agent_name,
            tool_name=tool_name,
            parameters=arguments,
            context=exec_context,
        )

        await self._audit_tool_call(
            agent_name=agent_name,
            tenant_id=tenant_id,
            user_id=user_id,
            tool_name=tool_name,
            authorized=call_record.authorized,
            error=call_record.error,
        )

        if not call_record.authorized:
            return _jsonrpc_err(
                req_id,
                ERR_FORBIDDEN,
                call_record.error or "Tool call denied by Cedar policy",
                data={"tool": tool_name, "agent": agent_name},
            )

        if call_record.error is not None:
            return _jsonrpc_ok(
                req_id,
                {
                    "content": [{"type": "text", "text": call_record.error}],
                    "isError": True,
                },
            )

        if isinstance(call_record.result, str):
            text_out = call_record.result
        else:
            text_out = json.dumps(call_record.result, default=str)
        return _jsonrpc_ok(
            req_id,
            {
                "content": [{"type": "text", "text": text_out}],
                "isError": False,
            },
        )

    async def _audit_tool_call(
        self,
        *,
        agent_name: str,
        tenant_id: str,
        user_id: str,
        tool_name: str,
        authorized: bool,
        error: str | None,
    ) -> None:
        if self.audit_logger is None:
            return
        result_label = "success" if (authorized and error is None) else "denied"
        await self.audit_logger.log(
            AuditEvent(
                event_type=(
                    AuditEventType.CODE_EXECUTED
                    if authorized and error is None
                    else AuditEventType.USER_LOGIN_FAILED
                ),
                timestamp=datetime.now(UTC),
                user_id=user_id,
                subject_id=None,
                action=f"MCP tools/call '{tool_name}' by agent '{agent_name}'",
                resource_type="mcp_tool",
                resource_id=tool_name,
                result=result_label,
                metadata={
                    "tenant_id": tenant_id,
                    "agent": agent_name,
                    "authorized": authorized,
                    "error": error,
                },
            )
        )

    async def serve_stdio(
        self,
        stdin: TextIO,
        stdout: TextIO,
        *,
        agent_name: str = "discovery",
        tenant_id: str = "default",
    ) -> int:
        """Process newline-delimited JSON-RPC 2.0 messages over stdio."""
        processed = 0
        for raw_line in stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                err_resp = _jsonrpc_err(None, ERR_PARSE_ERROR, f"Parse error: {exc}")
                stdout.write(json.dumps(err_resp) + "\n")
                stdout.flush()
                processed += 1
                continue

            resp = await self.handle_jsonrpc(
                payload,
                agent_name=agent_name,
                tenant_id=tenant_id,
            )
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
            processed += 1
        return processed


class MCPToolProxy:
    """
    Adapts a remote/local MCP tool to the PVS ``Tool`` protocol so it can be
    registered inside ``ToolRegistry`` and governed by Cedar + OWASP ASI01.
    """

    def __init__(
        self,
        *,
        client: MCPClient,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self._client = client
        self._name = name
        self._description = description
        self._parameters = parameters

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> Any:
        return await self._client.call_tool(self._name, kwargs)


class MCPClient:
    """
    MCP JSON-RPC 2.0 client supporting both in-process ``MCPServer`` and remote
    HTTP MCP endpoints (via ``httpx.AsyncClient``).
    """

    def __init__(
        self,
        *,
        server_url: str | None = None,
        local_server: MCPServer | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        agent_name: str = "discovery",
        tenant_id: str = "default",
    ) -> None:
        if server_url is None and local_server is None:
            raise ValueError("Provide either 'server_url' or 'local_server'")
        self.server_url = server_url
        self._local_server = local_server
        self.agent_name = agent_name
        self.tenant_id = tenant_id
        self._req_counter = 0
        self._client: httpx.AsyncClient | None = None
        if server_url is not None:
            client_kwargs: dict[str, Any] = {
                "headers": {"Content-Type": "application/json", **(headers or {})},
                "timeout": httpx.Timeout(timeout),
            }
            if transport is not None:
                client_kwargs["transport"] = transport
            self._client = httpx.AsyncClient(**client_kwargs)

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._req_counter += 1
        req_payload = {
            "jsonrpc": "2.0",
            "id": self._req_counter,
            "method": method,
            "params": params or {},
        }
        if self._local_server is not None:
            resp = await self._local_server.handle_jsonrpc(
                req_payload,
                agent_name=self.agent_name,
                tenant_id=self.tenant_id,
            )
        else:
            assert self._client is not None and self.server_url is not None
            http_resp = await self._client.post(self.server_url, json=req_payload)
            http_resp.raise_for_status()
            resp = http_resp.json()

        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"MCP error {err.get('code')}: {err.get('message')}")
        result: dict[str, Any] = resp.get("result", {})
        return result

    async def initialize(self) -> dict[str, Any]:
        return await self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "clientInfo": {"name": "pvs-mcp-client", "version": __version__},
            },
        )

    async def list_tools(self) -> list[dict[str, Any]]:
        res = await self._rpc("tools/list")
        tools = res.get("tools", [])
        return list(tools) if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        res = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        contents = res.get("content", [])
        text = "\n".join(
            str(item.get("text", ""))
            for item in contents
            if isinstance(item, dict) and item.get("type") == "text"
        )
        if res.get("isError"):
            raise RuntimeError(text or f"MCP tool '{name}' returned an error")
        return text

    async def discover_tools(self) -> list[MCPToolProxy]:
        """Discover remote/local MCP tools and wrap each as a PVS ``Tool`` proxy."""
        raw_tools = await self.list_tools()
        proxies: list[MCPToolProxy] = []
        for item in raw_tools:
            proxies.append(
                MCPToolProxy(
                    client=self,
                    name=str(item["name"]),
                    description=str(item.get("description", "")),
                    parameters=dict(item.get("inputSchema") or {"type": "object"}),
                )
            )
        return proxies

    async def register_into(self, registry: ToolRegistry) -> list[str]:
        """Discover MCP tools and register them into a Cedar-governed ``ToolRegistry``."""
        proxies = await self.discover_tools()
        names: list[str] = []
        for proxy in proxies:
            registry.register(proxy)
            names.append(proxy.name)
        return names

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()

"""
Open agent interoperability protocols (EVO-P3-07):
- MCP (Model Context Protocol): Cedar-governed JSON-RPC 2.0 Server & Client
- A2A (Agent-to-Agent Protocol): Agent Cards (``/.well-known/agent.json``) & Task Dispatcher
"""

from product_value_system.protocols.a2a import (
    A2AMessage,
    A2ARegistry,
    A2ATask,
    A2ATaskDispatcher,
    A2ATaskState,
    AgentAuthentication,
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from product_value_system.protocols.mcp import (
    MCPClient,
    MCPPrompt,
    MCPResource,
    MCPServer,
    MCPToolProxy,
)

__all__ = [
    "A2AMessage",
    "A2ARegistry",
    "A2ATask",
    "A2ATaskDispatcher",
    "A2ATaskState",
    "AgentAuthentication",
    "AgentCapabilities",
    "AgentCard",
    "AgentSkill",
    "MCPClient",
    "MCPPrompt",
    "MCPResource",
    "MCPServer",
    "MCPToolProxy",
]

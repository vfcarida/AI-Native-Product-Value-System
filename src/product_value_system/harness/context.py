"""
Execution context for agents.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExecutionContext:
    """
    Context provided to agent during execution.
    """

    run_id: str
    stage_name: str
    agent_name: str

    # System prompt components
    system_prompt: str
    examples: list[dict[str, Any]] = field(default_factory=list)

    # Tool guidance
    tool_guidance: dict[str, str] = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)

    # Workspace
    workspace_path: Path | None = None

    # Artifacts
    input_artifacts: dict[str, Any] = field(default_factory=dict)
    output_artifacts: dict[str, Any] = field(default_factory=dict)

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "run_id": self.run_id,
            "stage_name": self.stage_name,
            "agent_name": self.agent_name,
            "system_prompt": self.system_prompt,
            "examples": self.examples,
            "tool_guidance": self.tool_guidance,
            "allowed_tools": self.allowed_tools,
            "workspace_path": str(self.workspace_path) if self.workspace_path else None,
            "metadata": self.metadata,
        }

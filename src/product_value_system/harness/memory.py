"""
Memory management for agents.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """Single message in conversation."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class Memory:
    """
    Manages agent memory (conversation + artifacts).
    """

    def __init__(self, max_messages: int = 100) -> None:
        """
        Initialize memory.

        Args:
            max_messages: Maximum conversation history length
        """
        self.max_messages = max_messages
        self.messages: list[Message] = []
        self.artifacts: dict[str, Any] = {}

    def add_message(
        self,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Add message to conversation history.

        Args:
            role: Message role (system, user, assistant, tool)
            content: Message content
            metadata: Optional metadata
        """
        message = Message(role=role, content=content, metadata=metadata or {})
        self.messages.append(message)

        if len(self.messages) > self.max_messages:
            system_msgs = [m for m in self.messages if m.role == "system"]
            other_msgs = [m for m in self.messages if m.role != "system"]

            keep_count = max(0, self.max_messages - len(system_msgs))
            self.messages = system_msgs + (other_msgs[-keep_count:] if keep_count > 0 else [])

    def get_messages(self) -> list[Message]:
        """Get conversation history."""
        return self.messages.copy()

    def clear_messages(self) -> None:
        """Clear conversation history."""
        self.messages.clear()

    def store_artifact(self, key: str, value: Any) -> None:
        """Store artifact in long-term memory."""
        self.artifacts[key] = value

    def retrieve_artifact(self, key: str) -> Any | None:
        """Retrieve artifact from long-term memory."""
        return self.artifacts.get(key)

    def list_artifacts(self) -> list[str]:
        """List artifact keys."""
        return list(self.artifacts.keys())

    def to_dict(self) -> dict[str, Any]:
        """Serialize memory state."""
        return {
            "messages": [
                {"role": m.role, "content": m.content, "metadata": m.metadata}
                for m in self.messages
            ],
            "artifacts": self.artifacts,
        }

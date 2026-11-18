"""
Agent configuration loading from YAML.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AgentConfig:
    """
    Agent configuration from YAML.
    """

    name: str
    version: str
    objective: str

    capabilities: list[str]
    required_tools: list[str]

    model_provider: str
    model_name: str
    temperature: float

    allowed_tools: list[str]
    denied_tools: list[str]

    max_iterations: int
    max_cost_usd: float
    max_duration_seconds: int

    input_schema: str
    output_schema: str

    system_prompt: str
    examples: list[dict[str, Any]]

    @classmethod
    def from_yaml(cls, path: Path | str) -> "AgentConfig":
        """
        Load agent configuration from YAML file.

        Args:
            path: Path to YAML file

        Returns:
            AgentConfig instance

        Raises:
            FileNotFoundError: If file not found
            ValueError: If YAML invalid
        """
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid YAML configuration in {config_path}")

        metadata = data.get("metadata", {})
        spec = data.get("spec", {})
        model = spec.get("model", {})
        autonomy = spec.get("autonomy", {})

        return cls(
            name=str(metadata.get("name", "unknown")),
            version=str(metadata.get("version", "0.1.0")),
            objective=str(spec.get("objective", "")),
            capabilities=list(spec.get("capabilities", [])),
            required_tools=list(spec.get("requiredTools", [])),
            model_provider=str(model.get("provider", "openai")),
            model_name=str(model.get("model", "gpt-4")),
            temperature=float(model.get("temperature", 0.2)),
            allowed_tools=list(spec.get("allowedTools", [])),
            denied_tools=list(spec.get("deniedTools", [])),
            max_iterations=int(autonomy.get("maxIterations", 5)),
            max_cost_usd=float(autonomy.get("maxCostUsd", 1.0)),
            max_duration_seconds=int(autonomy.get("maxDurationSeconds", 300)),
            input_schema=str(spec.get("inputSchema", "")),
            output_schema=str(spec.get("outputSchema", "")),
            system_prompt=str(spec.get("systemPrompt", "")),
            examples=list(spec.get("examples", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize config to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "objective": self.objective,
            "capabilities": self.capabilities,
            "required_tools": self.required_tools,
            "model": {
                "provider": self.model_provider,
                "model": self.model_name,
                "temperature": self.temperature,
            },
            "allowed_tools": self.allowed_tools,
            "max_iterations": self.max_iterations,
            "max_cost_usd": self.max_cost_usd,
        }

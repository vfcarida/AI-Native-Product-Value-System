"""
Agent registry for management and discovery.
"""

from pathlib import Path

from product_value_system.agents.agent import Agent
from product_value_system.agents.config_loader import AgentConfig


class AgentRegistry:
    """
    Registry of available agents.
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        """
        Initialize agent registry.

        Args:
            config_dir: Directory containing agent YAML configs
        """
        self.config_dir = config_dir or Path("config/agents")
        self.agents: dict[str, Agent] = {}
        self.configs: dict[str, AgentConfig] = {}

    def register(self, agent: Agent, config: AgentConfig | None = None) -> None:
        """
        Register an agent.

        Args:
            agent: Agent instance
            config: Optional configuration
        """
        self.agents[agent.name] = agent
        if config:
            self.configs[agent.name] = config

    def unregister(self, name: str) -> None:
        """Unregister agent by name."""
        self.agents.pop(name, None)
        self.configs.pop(name, None)

    def get(self, name: str) -> Agent | None:
        """Get agent by name."""
        return self.agents.get(name)

    def get_config(self, name: str) -> AgentConfig | None:
        """Get agent configuration."""
        return self.configs.get(name)

    def list_agents(self) -> list[str]:
        """List all registered agent names."""
        return list(self.agents.keys())

    def list_by_capability(self, capability: str) -> list[Agent]:
        """
        Find agents with specified capability.

        Args:
            capability: Capability string

        Returns:
            List of agents with that capability
        """
        return [
            agent
            for agent in self.agents.values()
            if capability in agent.capabilities
        ]

    def load_configs(self) -> int:
        """
        Load all agent configurations from config_dir.

        Returns:
            Number of configs loaded
        """
        if not self.config_dir.exists():
            return 0

        count = 0
        for path in sorted(self.config_dir.glob("*.yaml")):
            try:
                config = AgentConfig.from_yaml(path)
                self.configs[config.name] = config
                count += 1
            except Exception as e:
                print(f"Warning: Failed to load {path}: {e}")

        return count

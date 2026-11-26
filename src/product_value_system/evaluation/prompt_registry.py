"""
Versioned YAML/Git PromptRegistry for reproducible AI engineering (EVO-P3-13).

Every prompt template carries a semantic version (``1.0.0``), agent affinity,
parameter contract, and canonical SHA-256 content digest that integrates directly
with ``PromptCache(prompt_version=...)`` and HMAC audit trails.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _parse_semver(version: str) -> tuple[int, int, int, str]:
    clean = version.strip().lstrip("v")
    parts = clean.split("-", 1)
    core = parts[0].split(".")
    nums = [int(p) if p.isdigit() else 0 for p in core[:3]]
    while len(nums) < 3:
        nums.append(0)
    suffix = parts[1] if len(parts) > 1 else ""
    return (nums[0], nums[1], nums[2], suffix)


@dataclass(frozen=True)
class PromptTemplate:
    """Immutable versioned prompt template with safe placeholder rendering."""

    id: str
    version: str
    agent: str
    system_prompt: str
    user_template: str
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def required_variables(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER_RE.findall(self.user_template))

    @property
    def content_sha256(self) -> str:
        raw = f"{self.id}@{self.version}\n{self.system_prompt}\n---\n{self.user_template}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def cache_version_key(self) -> str:
        return f"{self.id}@{self.version}:{self.content_sha256[:12]}"

    def render(self, **variables: Any) -> tuple[str, str]:
        """Render ``(system_prompt, user_prompt)`` validating required placeholders."""
        missing = [var for var in sorted(self.required_variables) if var not in variables]
        if missing:
            raise KeyError(
                f"Prompt '{self.id}@{self.version}' missing required variable(s): "
                f"{', '.join(missing)}"
            )

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            return str(variables[key])

        rendered_user = _PLACEHOLDER_RE.sub(_replace, self.user_template)
        return self.system_prompt, rendered_user


class PromptRegistry:
    """In-memory / YAML-backed registry of versioned prompt templates."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[str, PromptTemplate]] = {}

    @classmethod
    def from_directory(cls, directory: Path | str) -> PromptRegistry:
        """Load all ``*.yaml`` / ``*.yml`` prompt bundles from ``directory``."""
        registry = cls()
        root = Path(directory)
        if not root.is_dir():
            return registry
        for yaml_file in sorted(list(root.glob("*.yaml")) + list(root.glob("*.yml"))):
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8")) or {}
            items = data.get("prompts", [data] if "id" in data else [])
            for item in items:
                if not isinstance(item, dict) or "id" not in item:
                    continue
                registry.register(
                    PromptTemplate(
                        id=str(item["id"]),
                        version=str(item.get("version", "1.0.0")),
                        agent=str(item.get("agent", "general")),
                        system_prompt=str(item.get("system_prompt", "")),
                        user_template=str(item.get("user_template", "")),
                        description=str(item.get("description", "")),
                        metadata=dict(item.get("metadata") or {}),
                    )
                )
        return registry

    def register(self, template: PromptTemplate) -> None:
        self._templates.setdefault(template.id, {})[template.version] = template

    def get(self, prompt_id: str, *, version: str | None = None) -> PromptTemplate:
        versions = self._templates.get(prompt_id)
        if not versions:
            raise KeyError(f"Prompt '{prompt_id}' not found in registry")
        if version is not None:
            if version not in versions:
                raise KeyError(f"Prompt '{prompt_id}' version '{version}' not found")
            return versions[version]
        latest_ver = max(versions.keys(), key=_parse_semver)
        return versions[latest_ver]

    def list_prompts(self) -> list[str]:
        return sorted(self._templates.keys())

    def list_versions(self, prompt_id: str) -> list[str]:
        versions = self._templates.get(prompt_id, {})
        return sorted(versions.keys(), key=_parse_semver)

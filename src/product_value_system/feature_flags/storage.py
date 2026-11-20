"""
YAML storage and JSON Schema validation for feature flag configurations (EVO-015).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from product_value_system.feature_flags.models import FeatureFlag

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "feature_flags.schema.json"
FLAG_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class FlagConfigError(ValueError):
    """Raised when a feature flag configuration file is malformed or invalid."""


def _load_schema(schema_path: Path | None = None) -> dict[str, Any]:
    path = schema_path or DEFAULT_SCHEMA_PATH
    if not path.exists():
        raise FlagConfigError(f"Feature flags JSON schema not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise FlagConfigError(f"Feature flags schema must be a JSON object: {path}")
    return data


def validate_flags_payload(payload: Any, schema_path: Path | None = None) -> None:
    """Validate parsed ``flags.yaml`` payload against JSON Schema and semantic rules."""
    if not isinstance(payload, dict):
        raise FlagConfigError("Feature flags config must be a YAML mapping with top-level 'flags'")

    schema = _load_schema(schema_path)
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as exc:
        raise FlagConfigError(f"Invalid feature flags configuration: {exc.message}") from exc

    seen_names: set[str] = set()
    for raw_flag in payload["flags"]:
        name = str(raw_flag["name"])
        if not FLAG_NAME_PATTERN.match(name):
            raise FlagConfigError(f"Invalid flag name '{name}'")
        if name in seen_names:
            raise FlagConfigError(f"Duplicate feature flag name '{name}'")
        seen_names.add(name)

        for rule in raw_flag.get("targeting_rules", []):
            rtype = rule["type"]
            if rtype == "user_id" and not isinstance(rule.get("values"), list):
                raise FlagConfigError(
                    f"Flag '{name}': user_id targeting rule requires a 'values' list"
                )
            if rtype == "percentage":
                val = rule.get("value")
                if not isinstance(val, int | float) or not 0.0 <= float(val) <= 1.0:
                    raise FlagConfigError(
                        f"Flag '{name}': percentage rule value must be within [0.0, 1.0]"
                    )
            if rtype == "environment" and not isinstance(rule.get("value"), str):
                raise FlagConfigError(
                    f"Flag '{name}': environment targeting rule requires a string 'value'"
                )


class FlagStorage:
    """Load and persist feature flags in schema-validated YAML files."""

    def __init__(self, path: Path | str, schema_path: Path | None = None) -> None:
        self.path = Path(path)
        self.schema_path = schema_path

    def load(self) -> dict[str, FeatureFlag]:
        """Load and validate feature flags from ``self.path``."""
        if not self.path.exists():
            raise FileNotFoundError(f"Feature flags file not found: {self.path}")
        raw_text = self.path.read_text(encoding="utf-8")
        try:
            payload = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            raise FlagConfigError(f"Failed to parse YAML in {self.path}: {exc}") from exc

        validate_flags_payload(payload, schema_path=self.schema_path)
        flags: dict[str, FeatureFlag] = {}
        for item in payload["flags"]:
            flag = FeatureFlag.from_dict(item)
            flags[flag.name] = flag
        return flags

    def save(self, flags: dict[str, FeatureFlag]) -> None:
        """Validate and atomically write ``flags`` back to ``self.path``."""
        payload = {"flags": [flag.to_dict() for flag in flags.values()]}
        validate_flags_payload(payload, schema_path=self.schema_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(serialized, encoding="utf-8")
        tmp_path.replace(self.path)


def load_flags(path: Path | str = "flags.yaml") -> dict[str, FeatureFlag]:
    """Convenience loader for feature flags YAML."""
    return FlagStorage(path).load()


def save_flags(flags: dict[str, FeatureFlag], path: Path | str = "flags.yaml") -> None:
    """Convenience saver for feature flags YAML."""
    FlagStorage(path).save(flags)

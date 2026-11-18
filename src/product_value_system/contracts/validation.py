"""
Schema validation utilities for JSON Schema 2020-12 contracts.
"""

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator


class SchemaValidator:
    """Validates artifacts against JSON Schema 2020-12 contracts."""

    def __init__(self, schema_dir: Path | str) -> None:
        """
        Initialize validator with schema directory.

        Args:
            schema_dir: Path to directory containing .schema.json files
        """
        self.schema_dir = Path(schema_dir)
        self._schemas: dict[str, dict[str, Any]] = {}
        self._aliases: dict[str, str] = {}
        self._load_schemas()

    def _load_schemas(self) -> None:
        """Load and verify all JSON Schema 2020-12 contracts from schema directory."""
        for schema_file in sorted(self.schema_dir.glob("*.schema.json")):
            with open(schema_file, encoding="utf-8") as f:
                schema: dict[str, Any] = json.load(f)

            Draft202012Validator.check_schema(schema)

            filename = schema_file.name
            self._schemas[filename] = schema
            self._aliases[filename] = filename

            # Also register full $id URI and short base name as lookup aliases
            schema_uri = schema.get("$id")
            if isinstance(schema_uri, str):
                self._aliases[schema_uri] = filename

            short_name = filename.removesuffix(".schema.json")
            self._aliases[short_name] = filename

    def _resolve_schema(self, schema_id: str) -> dict[str, Any]:
        """Resolve a schema by filename, $id URI, or short name."""
        if schema_id in self._schemas:
            return self._schemas[schema_id]
        if schema_id in self._aliases:
            return self._schemas[self._aliases[schema_id]]
        raise KeyError(f"Schema not found: {schema_id}")

    def validate(
        self,
        instance: dict[str, Any],
        schema_id: str,
    ) -> None:
        """
        Validate instance against schema.

        Args:
            instance: Data to validate
            schema_id: Schema identifier (e.g., "problem.schema.json" or full $id URI)

        Raises:
            jsonschema.ValidationError: If validation fails
            KeyError: If schema not found
        """
        schema = self._resolve_schema(schema_id)
        validator = Draft202012Validator(
            schema,
            format_checker=Draft202012Validator.FORMAT_CHECKER,
        )
        validator.validate(instance)

    def is_valid(
        self,
        instance: dict[str, Any],
        schema_id: str,
    ) -> bool:
        """
        Check if instance is valid without raising ValidationError.

        Returns:
            True if valid, False otherwise
        """
        try:
            self.validate(instance, schema_id)
            return True
        except jsonschema.ValidationError:
            return False

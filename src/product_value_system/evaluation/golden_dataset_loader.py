"""
Golden dataset loader, schema validator, and integrity utilities (EVO-011).

Supports versioned JSONL golden datasets organized by agent:
    golden_datasets/
    ├── discovery/dataset_v1.jsonl
    ├── specification/dataset_v1.jsonl
    ├── build/dataset_v1.jsonl
    ├── review/dataset_v1.jsonl
    ├── brownfield/dataset_v1.jsonl
    └── schema.json
"""

import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

logger = logging.getLogger(__name__)


@dataclass
class GoldenSample:
    """Single sample from a versioned JSONL golden dataset."""

    id: str
    version: int
    category: str
    tags: list[str]
    input: dict[str, Any]
    expected_output: dict[str, Any]
    evaluation_rubric: dict[str, Any]
    pass_threshold: float = 0.75
    created: str | None = None
    validated: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def input_artifact(self) -> dict[str, Any]:
        """Compatibility alias matching legacy ``GoldenSample.input_artifact``."""
        return self.input

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoldenSample":
        """Parse a ``GoldenSample`` from a JSON object."""
        return cls(
            id=str(data["id"]),
            version=int(data.get("version", 1)),
            category=str(data.get("category", "unknown")),
            tags=[str(t) for t in data.get("tags", [])],
            input=dict(data["input"]),
            expected_output=dict(data["expected_output"]),
            evaluation_rubric=dict(data["evaluation_rubric"]),
            pass_threshold=float(data.get("pass_threshold", 0.7)),
            created=data.get("created"),
            validated=data.get("validated"),
            metadata=dict(data.get("metadata", {})) if data.get("metadata") else {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize sample to JSONL-compatible dictionary."""
        payload: dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "created": self.created or "2026-11-19T10:00:00Z",
            "validated": self.validated or "2026-11-19T12:00:00Z",
            "category": self.category,
            "tags": list(self.tags),
            "input": self.input,
            "expected_output": self.expected_output,
            "evaluation_rubric": self.evaluation_rubric,
            "pass_threshold": self.pass_threshold,
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass
class GoldenDataset:
    """Container for golden dataset samples with filtering and subsetting utilities."""

    agent_name: str
    version: int
    samples: list[GoldenSample]
    checksum: str | None = None

    def __len__(self) -> int:
        return len(self.samples)

    def filter_by_category(self, category: str) -> "GoldenDataset":
        """Return a new dataset filtered to a single HELM category."""
        filtered = [s for s in self.samples if s.category == category]
        return GoldenDataset(
            agent_name=self.agent_name,
            version=self.version,
            samples=filtered,
            checksum=self.checksum,
        )

    def filter_by_tags(self, tags: list[str]) -> "GoldenDataset":
        """Return a new dataset containing samples matching any tag."""
        tag_set = set(tags)
        filtered = [s for s in self.samples if tag_set.intersection(s.tags)]
        return GoldenDataset(
            agent_name=self.agent_name,
            version=self.version,
            samples=filtered,
            checksum=self.checksum,
        )

    def sample_subset(self, n: int, seed: int = 42) -> "GoldenDataset":
        """Return a deterministic random subset of up to ``n`` samples (for fast CI eval)."""
        rng = random.Random(seed)
        k = min(max(0, n), len(self.samples))
        sampled = rng.sample(self.samples, k)
        return GoldenDataset(
            agent_name=self.agent_name,
            version=self.version,
            samples=sampled,
            checksum=self.checksum,
        )

    def category_distribution(self) -> dict[str, int]:
        """Count samples per category."""
        counts: dict[str, int] = {}
        for sample in self.samples:
            counts[sample.category] = counts.get(sample.category, 0) + 1
        return counts


class GoldenDatasetLoader:
    """
    Load, validate, persist, and hash versioned JSONL golden datasets.

    Example::

        loader = GoldenDatasetLoader("golden_datasets")
        dataset = loader.load("discovery", version=1)
        print(f"Loaded {len(dataset)} samples")
    """

    def __init__(
        self,
        root_path: Path | str,
        schema_path: Path | str | None = None,
    ) -> None:
        self.root_path = Path(root_path)
        if not self.root_path.exists():
            raise ValueError(f"Golden dataset root not found: {self.root_path}")

        resolved_schema = (
            Path(schema_path) if schema_path is not None else self.root_path / "schema.json"
        )
        self._validator: Draft202012Validator | None = None
        if resolved_schema.exists():
            schema_data = json.loads(resolved_schema.read_text(encoding="utf-8"))
            self._validator = Draft202012Validator(schema_data)

    def _dataset_file(self, agent_name: str, version: int = 1) -> Path:
        return self.root_path / agent_name / f"dataset_v{version}.jsonl"

    def compute_checksum(self, agent_name: str, version: int = 1) -> str:
        """Compute SHA-256 checksum of a golden dataset file for integrity verification."""
        dataset_file = self._dataset_file(agent_name, version)
        if not dataset_file.exists():
            raise FileNotFoundError(
                f"Golden dataset not found: {dataset_file}\n"
                f"Available agents: {self.list_agents()}"
            )
        digest = hashlib.sha256(dataset_file.read_bytes()).hexdigest()
        return digest

    def validate_sample_dict(self, data: dict[str, Any]) -> None:
        """Validate a parsed JSON sample against ``golden_datasets/schema.json`` if loaded."""
        if self._validator is None:
            return
        self._validator.validate(data)

    def validate_dataset(self, agent_name: str, version: int = 1) -> list[str]:
        """
        Validate every line in a JSONL dataset file and check ID uniqueness & rubric weights.

        Returns:
            List of human-readable error strings (empty when valid).
        """
        dataset_file = self._dataset_file(agent_name, version)
        if not dataset_file.exists():
            return [f"Golden dataset not found: {dataset_file}"]

        errors: list[str] = []
        seen_ids: set[str] = set()

        with open(dataset_file, encoding="utf-8") as f:
            for line_num, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append(f"Line {line_num}: invalid JSON: {exc}")
                    continue

                if self._validator is not None:
                    try:
                        self._validator.validate(data)
                    except ValidationError as exc:
                        errors.append(f"Line {line_num}: schema error: {exc.message}")

                sample_id = str(data.get("id", ""))
                if sample_id in seen_ids:
                    errors.append(f"Line {line_num}: duplicate sample id '{sample_id}'")
                seen_ids.add(sample_id)

                rubric = data.get("evaluation_rubric", {})
                if isinstance(rubric, dict) and rubric:
                    weight_sum = sum(
                        float(v.get("weight", 0.0))
                        for v in rubric.values()
                        if isinstance(v, dict)
                    )
                    if abs(weight_sum - 1.0) > 0.05:
                        errors.append(
                            f"Line {line_num} ({sample_id}): rubric weights sum to "
                            f"{weight_sum:.2f} (expected ~1.0)"
                        )

        return errors

    def load(
        self,
        agent_name: str,
        version: int = 1,
        categories: list[str] | None = None,
        tags: list[str] | None = None,
        strict: bool = False,
    ) -> GoldenDataset:
        """
        Load golden dataset for a specific agent.

        Args:
            agent_name: ``discovery``, ``specification``, ``build``, ``review``, or ``brownfield``
            version: Dataset version number
            categories: Optional list of categories to keep
            tags: Optional list of tags to match (OR semantics)
            strict: If True, raise on invalid JSON or schema errors instead of logging/skipping

        Returns:
            ``GoldenDataset`` containing filtered ``GoldenSample`` items
        """
        dataset_file = self._dataset_file(agent_name, version)
        if not dataset_file.exists():
            raise FileNotFoundError(
                f"Golden dataset not found: {dataset_file}\n"
                f"Available agents: {self.list_agents()}"
            )

        checksum = self.compute_checksum(agent_name, version)
        samples: list[GoldenSample] = []

        with open(dataset_file, encoding="utf-8") as f:
            for line_num, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if strict and self._validator is not None:
                        self._validator.validate(data)
                    sample = GoldenSample.from_dict(data)

                    if categories and sample.category not in categories:
                        continue
                    if tags and not any(t in sample.tags for t in tags):
                        continue

                    samples.append(sample)
                except json.JSONDecodeError as exc:
                    if strict:
                        raise ValueError(f"Invalid JSON at line {line_num}: {exc}") from exc
                    logger.error("Invalid JSON at line %d: %s", line_num, exc)
                except Exception as exc:
                    if strict:
                        raise
                    logger.error("Failed to parse sample at line %d: %s", line_num, exc)

        logger.info(
            "Loaded %d samples for %s (v%d, filters: categories=%s, tags=%s)",
            len(samples),
            agent_name,
            version,
            categories,
            tags,
        )

        return GoldenDataset(
            agent_name=agent_name,
            version=version,
            samples=samples,
            checksum=checksum,
        )

    def save_sample(
        self,
        agent_name: str,
        sample: GoldenSample,
        version: int = 1,
        validate: bool = True,
    ) -> Path:
        """Append a single ``GoldenSample`` to ``<agent_name>/dataset_v<version>.jsonl``."""
        payload = sample.to_dict()
        if validate:
            self.validate_sample_dict(payload)

        dataset_file = self._dataset_file(agent_name, version)
        dataset_file.parent.mkdir(parents=True, exist_ok=True)
        with open(dataset_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return dataset_file

    def list_agents(self) -> list[str]:
        """List available agent dataset directories sorted alphabetically."""
        return sorted(
            d.name
            for d in self.root_path.iterdir()
            if d.is_dir() and not d.name.startswith(".") and any(d.glob("dataset_v*.jsonl"))
        )

    def list_versions(self, agent_name: str) -> list[int]:
        """List available dataset versions for an agent."""
        agent_dir = self.root_path / agent_name
        if not agent_dir.exists():
            return []

        versions: list[int] = []
        for file_path in agent_dir.glob("dataset_v*.jsonl"):
            try:
                version = int(file_path.stem.split("_v")[1])
                versions.append(version)
            except (IndexError, ValueError):
                continue

        return sorted(versions)

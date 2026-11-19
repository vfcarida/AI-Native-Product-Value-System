"""
Golden dataset support (expected inputs/outputs).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GoldenSample:
    """A single golden dataset sample."""

    id: str
    input_artifact: dict[str, Any]
    expected_output: dict[str, Any]
    metadata: dict[str, Any] | None = None


class GoldenDatasetLoader:
    """Loads golden datasets from disk."""

    def __init__(self, dataset_dir: Path | str) -> None:
        self.dataset_dir = Path(dataset_dir)

    def load_dataset(self, dataset_name: str) -> list[GoldenSample]:
        """Load a golden dataset by name."""
        dataset_path = self.dataset_dir / dataset_name

        if not dataset_path.exists():
            raise FileNotFoundError(f"Dataset not found: {dataset_path}")

        samples: list[GoldenSample] = []

        for sample_file in sorted(dataset_path.glob("*.json")):
            with open(sample_file, encoding="utf-8") as f:
                data = json.load(f)

            sample = GoldenSample(
                id=str(data["id"]),
                input_artifact=data["input"],
                expected_output=data["expected_output"],
                metadata=data.get("metadata"),
            )
            samples.append(sample)

        return samples

    def save_sample(self, dataset_name: str, sample: GoldenSample) -> None:
        """Save a golden sample to dataset."""
        dataset_path = self.dataset_dir / dataset_name
        dataset_path.mkdir(parents=True, exist_ok=True)

        sample_file = dataset_path / f"{sample.id}.json"

        with open(sample_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "id": sample.id,
                    "input": sample.input_artifact,
                    "expected_output": sample.expected_output,
                    "metadata": sample.metadata,
                },
                f,
                indent=2,
            )

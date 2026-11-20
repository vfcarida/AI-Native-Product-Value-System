#!/usr/bin/env python3
"""
Golden Dataset Curation & Validation CLI (EVO-011).

Supports:
- Validating all golden datasets against ``golden_datasets/schema.json``,
  PVS artifact schemas, rubric weight sums, and ``SecretDetector``
- Displaying corpus statistics and SHA-256 integrity checksums
- Adding validated JSON samples from file or interactive prompts

Usage:
    python scripts/curate_golden_dataset.py --validate-all
    python scripts/curate_golden_dataset.py --stats
    python scripts/curate_golden_dataset.py --agent discovery --interactive
    python scripts/curate_golden_dataset.py --agent discovery --add-file sample.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from product_value_system.contracts.validation import SchemaValidator  # noqa: E402
from product_value_system.evaluation.golden_dataset_loader import (  # noqa: E402
    GoldenDatasetLoader,
    GoldenSample,
)
from product_value_system.security.secret_detection import SecretDetector  # noqa: E402

DEFAULT_RUBRICS: dict[str, dict[str, dict[str, Any]]] = {
    "discovery": {
        "hypothesis_measurable": {
            "weight": 0.3,
            "criteria": "Primary metric defines name, unit, baseline, target, and method",
        },
        "measurement_feasible": {
            "weight": 0.2,
            "criteria": "Data source and measurement method are practical",
        },
        "guardrails_present": {
            "weight": 0.2,
            "criteria": "At least one guardrail with threshold and direction",
        },
        "evidence_adequate": {
            "weight": 0.2,
            "criteria": "Evidence requirements specify concrete validation steps",
        },
        "outcome_focused": {
            "weight": 0.1,
            "criteria": "Hypothesis describes user/business outcome, not feature output",
        },
    },
    "specification": {
        "requirements_testable": {
            "weight": 0.3,
            "criteria": "Functional requirements have IDs and testable acceptance criteria",
        },
        "architecture_justified": {
            "weight": 0.25,
            "criteria": "Architecture decisions include rationale and trade-offs",
        },
        "quality_attributes_complete": {
            "weight": 0.2,
            "criteria": "Performance, security, scalability, reliability targets defined",
        },
        "testability_defined": {
            "weight": 0.15,
            "criteria": "Unit, integration, and acceptance test criteria included",
        },
        "constraints_respected": {
            "weight": 0.1,
            "criteria": "Technical, operational, and business constraints captured",
        },
    },
    "build": {
        "syntax_valid": {
            "weight": 0.25,
            "criteria": "Generated Python code and tests compile without SyntaxError",
        },
        "requirements_implemented": {
            "weight": 0.3,
            "criteria": "Functional requirements and acceptance criteria implemented",
        },
        "tests_passing": {
            "weight": 0.25,
            "criteria": "Automated tests pass inside sandbox",
        },
        "code_quality_and_security": {
            "weight": 0.2,
            "criteria": "No secrets, unsafe shell commands, or precision bugs",
        },
    },
    "review": {
        "verdict_accuracy": {
            "weight": 0.35,
            "criteria": "Review verdict matches severity of design or security flaws",
        },
        "findings_actionable": {
            "weight": 0.3,
            "criteria": "Findings include severity, category, description, recommendation",
        },
        "evidence_schema_valid": {
            "weight": 0.2,
            "criteria": "Output conforms to evidence.schema.json",
        },
        "specificity": {
            "weight": 0.15,
            "criteria": "References concrete requirement or decision identifiers",
        },
    },
    "brownfield": {
        "behavior_preservation": {
            "weight": 0.3,
            "criteria": "Preserves existing legacy behavior accurately",
        },
        "drift_detection": {
            "weight": 0.25,
            "criteria": "Identifies code-vs-documentation discrepancies",
        },
        "characterization_tests": {
            "weight": 0.25,
            "criteria": "Defines measurable characterization test coverage targets",
        },
        "migration_readiness": {
            "weight": 0.2,
            "criteria": "Provides measurable metrics and guardrails for modernization",
        },
    },
}


def validate_all_datasets(
    dataset_root: Path,
    schemas_dir: Path,
    version: int = 1,
) -> tuple[bool, dict[str, Any]]:
    """
    Validate all agent datasets for JSONL syntax, sample schema, nested PVS
    artifact schemas, rubric weights, and absence of secrets.
    """
    loader = GoldenDatasetLoader(dataset_root)
    schema_validator = SchemaValidator(schemas_dir)
    secret_detector = SecretDetector()

    report: dict[str, Any] = {"agents": {}, "total_samples": 0, "errors": []}
    agents = loader.list_agents()
    if not agents:
        report["errors"].append(f"No agent datasets found under {dataset_root}")
        return False, report

    all_ok = True
    for agent in agents:
        agent_errors = loader.validate_dataset(agent, version=version)
        dataset = loader.load(agent, version=version)

        for sample in dataset.samples:
            if "problem" in sample.input:
                try:
                    schema_validator.validate(sample.input["problem"], "problem.schema.json")
                except Exception as exc:
                    agent_errors.append(f"{sample.id}: invalid problem schema: {exc}")
            if "outcome" in sample.input:
                try:
                    schema_validator.validate(sample.input["outcome"], "outcome.schema.json")
                except Exception as exc:
                    agent_errors.append(f"{sample.id}: invalid outcome schema: {exc}")
            if "specification" in sample.input:
                try:
                    schema_validator.validate(
                        sample.input["specification"],
                        "specification.schema.json",
                    )
                except Exception as exc:
                    agent_errors.append(f"{sample.id}: invalid specification schema: {exc}")

        dataset_file = dataset_root / agent / f"dataset_v{version}.jsonl"
        secrets = secret_detector.find_secrets(dataset_file.read_text(encoding="utf-8"))
        if secrets:
            agent_errors.append(f"Secret detector flagged {len(secrets)} match(es)")

        if agent_errors:
            all_ok = False
            report["errors"].extend([f"[{agent}] {err}" for err in agent_errors])

        report["agents"][agent] = {
            "count": len(dataset),
            "distribution": dataset.category_distribution(),
            "checksum": dataset.checksum,
            "errors": agent_errors,
        }
        report["total_samples"] += len(dataset)

    return all_ok, report


def add_sample_from_file(
    dataset_root: Path,
    agent_name: str,
    sample_file: Path,
    version: int = 1,
) -> GoldenSample:
    """Validate, secret-scan, and append a sample JSON file to the dataset."""
    raw_text = sample_file.read_text(encoding="utf-8")
    secrets = SecretDetector().find_secrets(raw_text)
    if secrets:
        raise ValueError(f"Refusing to add sample: {len(secrets)} potential secret(s) detected")

    data = json.loads(raw_text)
    loader = GoldenDatasetLoader(dataset_root)
    loader.validate_sample_dict(data)
    sample = GoldenSample.from_dict(data)
    loader.save_sample(agent_name, sample, version=version, validate=True)
    return sample


def interactive_curate(
    dataset_root: Path,
    agent_name: str,
    version: int = 1,
) -> GoldenSample:
    """Interactively prompt a curator to author and append a new golden sample."""
    loader = GoldenDatasetLoader(dataset_root)
    existing_count = 0
    try:
        existing_count = len(loader.load(agent_name, version=version))
    except FileNotFoundError:
        existing_count = 0

    default_id = f"{agent_name}-{existing_count + 1:03d}"
    print(f"\nCurating new golden sample for '{agent_name}' (v{version})")
    print("-" * 55)

    sample_id = input(f"Sample ID [{default_id}]: ").strip() or default_id
    category = (
        input("Category (happy-path / edge-case / adversarial) [happy-path]: ").strip()
        or "happy-path"
    )
    tags_raw = input("Comma-separated tags [synthetic,curated]: ").strip() or "synthetic,curated"
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    threshold_raw = input("Pass threshold (0.0-1.0) [0.75]: ").strip() or "0.75"
    threshold = float(threshold_raw)

    statement = input("Problem / scenario summary (>=20 chars): ").strip()
    if len(statement) < 20:
        statement = (
            f"{statement} — curated evaluation scenario for {agent_name} quality validation."
        )

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    input_payload: dict[str, Any] = {
        "problem": {
            "id": f"problem-{sample_id}",
            "version": 1,
            "created": now,
            "statement": statement,
            "context": {
                "user_segment": input("User segment [Product users]: ").strip() or "Product users",
                "business_context": (
                    input("Business context [Quality validation]: ").strip()
                    or "Quality validation"
                ),
            },
            "constraints": {"no_pii": True, "synthetic_only": True},
        }
    }

    metric_name = input("Expected primary metric [primary_metric]: ").strip() or "primary_metric"
    expected_output: dict[str, Any] = (
        {"expected_behavior": "block_or_sanitize"}
        if category == "adversarial"
        else {"primary_metric": {"name": metric_name}}
    )

    sample = GoldenSample(
        id=sample_id,
        version=version,
        created=now,
        validated=now,
        category=category,
        tags=tags,
        input=input_payload,
        expected_output=expected_output,
        evaluation_rubric=DEFAULT_RUBRICS.get(agent_name, DEFAULT_RUBRICS["discovery"]),
        pass_threshold=threshold,
    )

    serialized = json.dumps(sample.to_dict())
    if SecretDetector().find_secrets(serialized):
        raise ValueError("Curated sample contains potential secrets; aborting save.")

    saved_path = loader.save_sample(agent_name, sample, version=version, validate=True)
    print(f"Saved validated sample '{sample.id}' to {saved_path}")
    return sample


def build_parser() -> argparse.ArgumentParser:
    """Create argument parser for the curation CLI."""
    parser = argparse.ArgumentParser(
        description="Golden dataset curation, validation, and integrity CLI (EVO-011)"
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=REPO_ROOT / "golden_datasets",
        help="Root directory of golden datasets (default: golden_datasets/)",
    )
    parser.add_argument(
        "--schemas-dir",
        type=Path,
        default=REPO_ROOT / "schemas",
        help="Directory containing PVS artifact JSON schemas",
    )
    parser.add_argument(
        "--version",
        type=int,
        default=1,
        help="Dataset version number (default: 1)",
    )
    parser.add_argument(
        "--validate-all",
        action="store_true",
        help="Validate all golden datasets against schemas and secret detector",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print golden dataset counts, distributions, and SHA-256 checksums",
    )
    parser.add_argument(
        "--agent",
        choices=["discovery", "specification", "build", "review", "brownfield"],
        help="Target agent dataset for adding samples",
    )
    parser.add_argument(
        "--add-file",
        type=Path,
        help="Path to JSON file containing a new golden sample to append",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Launch interactive prompt to author a new golden sample",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.validate_all or args.stats:
        ok, report = validate_all_datasets(
            args.dataset_root,
            args.schemas_dir,
            version=args.version,
        )
        print(f"Golden Datasets Summary (v{args.version}): {report['total_samples']} total samples")
        for agent, info in sorted(report["agents"].items()):
            checksum = (info["checksum"] or "")[:12]
            status = "OK" if not info["errors"] else f"FAIL ({len(info['errors'])})"
            print(
                f"  - {agent:14s}: {info['count']:2d} samples | "
                f"{info['distribution']} | sha256={checksum}... | {status}"
            )
        if report["errors"]:
            print("\nValidation Errors:")
            for err in report["errors"]:
                print(f"  ERROR: {err}")
            return 1
        return 0

    if args.add_file:
        if not args.agent:
            print("ERROR: --agent is required with --add-file", file=sys.stderr)
            return 1
        sample = add_sample_from_file(
            args.dataset_root,
            args.agent,
            args.add_file,
            version=args.version,
        )
        print(f"OK: Appended validated sample '{sample.id}' to {args.agent} v{args.version}")
        return 0

    if args.interactive:
        if not args.agent:
            print("ERROR: --agent is required with --interactive", file=sys.stderr)
            return 1
        interactive_curate(args.dataset_root, args.agent, version=args.version)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

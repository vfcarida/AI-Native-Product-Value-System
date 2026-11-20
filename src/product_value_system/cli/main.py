"""
CLI entry point for Product Value System.
"""

import argparse
import sys
from pathlib import Path

from product_value_system.__version__ import __version__
from product_value_system.cli.commands import (
    eval_command,
    init_command,
    list_command,
    measure_outcome_command,
    review_command,
    run_command,
    run_simulation_command,
    show_command,
    status_command,
    validate_command,
)


def create_parser() -> argparse.ArgumentParser:
    """Create CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="pvs",
        description="AI-native Product Value System",
        epilog="For more information, see SPECIFICATION.md and ARCHITECTURE.md",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="Configuration file (default: ~/.pvs/config.yaml)",
    )

    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # pvs init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize new project",
    )
    init_parser.add_argument(
        "--output",
        type=Path,
        default=Path("problem.yaml"),
        help="Output file (default: problem.yaml)",
    )

    # pvs run
    run_parser = subparsers.add_parser(
        "run",
        help="Execute value stream run",
    )
    run_parser.add_argument(
        "problem",
        type=Path,
        help="Problem statement file (YAML)",
    )
    run_parser.add_argument(
        "--config-override",
        type=str,
        help="Override config (key=value)",
    )
    run_parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Max cost per run in USD",
    )
    run_parser.add_argument(
        "--daily-budget",
        type=float,
        default=None,
        help="Max cumulative cost per day in USD",
    )
    run_parser.add_argument(
        "--monthly-budget",
        type=float,
        default=None,
        help="Max cumulative cost per month in USD",
    )
    run_parser.add_argument(
        "--enable-simulation",
        action="store_true",
        default=False,
        help="Enable TinyTroupe synthetic customer simulation during discovery",
    )
    run_parser.add_argument(
        "--simulation-budget",
        type=float,
        default=10.0,
        help="Max USD budget for synthetic customer simulation (default: 10.0)",
    )
    run_parser.add_argument(
        "--capture-trajectory",
        action="store_true",
        default=False,
        help="Capture ReAct execution trajectory during value stream run",
    )
    run_parser.add_argument(
        "--output",
        "--trajectory-output",
        dest="trajectory_output",
        type=Path,
        default=None,
        help="Optional JSON file path to save captured trajectory (with --capture-trajectory)",
    )
    run_parser.add_argument(
        "--multi-review",
        action="store_true",
        default=False,
        help="Run parallel 4-agent independent review (Security, Perf, Maint, Architecture)",
    )

    # pvs run-simulation
    sim_parser = subparsers.add_parser(
        "run-simulation",
        help="Run a synthetic customer simulation scenario",
    )
    sim_parser.add_argument(
        "scenario",
        type=Path,
        help="Scenario configuration file (YAML)",
    )
    sim_parser.add_argument(
        "--max-cost",
        type=float,
        default=10.0,
        help="Maximum simulation budget in USD (default: 10.0)",
    )
    sim_parser.add_argument(
        "--templates",
        type=Path,
        default=None,
        help="Optional path to persona_templates.yaml",
    )

    # pvs measure-outcome
    measure_parser = subparsers.add_parser(
        "measure-outcome",
        help="Measure post-launch production outcome and attribute costs (Learn stage)",
    )
    measure_parser.add_argument(
        "--outcome-id",
        required=True,
        help="Outcome identifier (e.g., outcome-acme-payment)",
    )
    measure_parser.add_argument(
        "--outcome-file",
        type=Path,
        default=None,
        help="Optional path to outcome.yaml containing success criteria",
    )
    measure_parser.add_argument(
        "--measurement-window",
        type=int,
        default=30,
        help="Post-deployment measurement window in days (default: 30)",
    )
    measure_parser.add_argument(
        "--baseline-window",
        type=int,
        default=30,
        help="Pre-deployment baseline window in days (default: 30)",
    )
    measure_parser.add_argument(
        "--use-mock-analytics",
        action="store_true",
        default=False,
        help="Use deterministic mock analytics adapter for local testing/demos",
    )
    measure_parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="SQL analytics connection URL (e.g., sqlite:///analytics.db)",
    )
    measure_parser.add_argument(
        "--prometheus-url",
        type=str,
        default=None,
        help="Prometheus HTTP API URL (e.g., http://localhost:9090)",
    )
    measure_parser.add_argument(
        "--metric",
        type=str,
        default=None,
        help="Primary metric name override (default: 'Merchant retention rate')",
    )
    measure_parser.add_argument(
        "--baseline",
        type=float,
        default=None,
        help="Baseline value override (default: 85.0)",
    )
    measure_parser.add_argument(
        "--target",
        type=float,
        default=None,
        help="Target value override (default: 97.75)",
    )
    measure_parser.add_argument(
        "--current-value",
        type=float,
        default=None,
        help="Current metric value when using --use-mock-analytics",
    )
    measure_parser.add_argument(
        "--dev-cost",
        type=float,
        default=12.50,
        help="Development + LLM cost in USD to attribute (default: 12.50)",
    )
    measure_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output YAML path for measurement artifact",
    )

    # pvs eval
    eval_parser = subparsers.add_parser(
        "eval",
        help="Run regression evaluation against golden datasets",
    )
    eval_parser.add_argument(
        "agent",
        choices=["discovery", "specification", "build", "review", "brownfield", "all"],
        help="Agent to evaluate (or 'all' for full golden suite)",
    )
    eval_parser.add_argument(
        "--dataset-version",
        type=int,
        default=1,
        help="Golden dataset version (default: 1)",
    )
    eval_parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=None,
        help="Golden dataset root directory (default: golden_datasets)",
    )
    eval_parser.add_argument(
        "--fast",
        action="store_true",
        default=False,
        help="Fast mode: evaluate deterministic subset of samples (for CI)",
    )
    eval_parser.add_argument(
        "--subset-size",
        type=int,
        default=10,
        help="Number of samples per agent in fast mode (default: 10)",
    )
    eval_parser.add_argument(
        "--category",
        choices=["happy-path", "edge-case", "adversarial"],
        default=None,
        help="Optional HELM category filter",
    )
    eval_parser.add_argument(
        "--threshold",
        type=float,
        default=0.75,
        help="Minimum pass rate threshold (default: 0.75)",
    )
    eval_parser.add_argument(
        "--baseline-dir",
        type=Path,
        default=Path(".eval_baselines"),
        help="Directory for regression baseline storage (default: .eval_baselines)",
    )
    eval_parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path (or directory when agent='all') for JSON eval reports",
    )
    eval_parser.add_argument(
        "--max-cost",
        type=float,
        default=10.0,
        help="Maximum evaluation USD budget warning/cap (default: 10.0)",
    )
    eval_parser.add_argument(
        "--use-mock",
        action="store_true",
        default=False,
        help="Force deterministic offline mock adapters ($0 cost)",
    )
    eval_parser.add_argument(
        "--with-trajectories",
        action="store_true",
        default=False,
        help="Enable trajectory capture and behavioral trajectory evaluation",
    )
    eval_parser.add_argument(
        "--trajectories-dir",
        type=Path,
        default=None,
        help="Directory of golden trajectories (default: golden_datasets/trajectories)",
    )

    # pvs review
    review_parser = subparsers.add_parser(
        "review",
        help="Run parallel 4-agent independent review (Elephant-Goldfish pattern)",
    )
    review_parser.add_argument(
        "specification",
        type=Path,
        help="Specification file (YAML or JSON)",
    )
    review_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional YAML/JSON path to save consolidated review report",
    )
    review_parser.add_argument(
        "--max-high",
        type=int,
        default=5,
        help="Maximum HIGH findings allowed before failing release gate (default: 5)",
    )
    review_parser.add_argument(
        "--use-mock",
        action="store_true",
        default=False,
        help="Force deterministic offline mock reviewers ($0 cost)",
    )

    # pvs status
    status_parser = subparsers.add_parser(
        "status",
        help="Show run status",
    )
    status_parser.add_argument(
        "run_id",
        help="Run ID",
    )

    # pvs list
    list_parser = subparsers.add_parser(
        "list",
        help="List recent runs",
    )
    list_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of runs to show (default: 10)",
    )

    # pvs show
    show_parser = subparsers.add_parser(
        "show",
        help="Show detailed run information",
    )
    show_parser.add_argument(
        "run_id",
        help="Run ID",
    )

    # pvs validate
    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate artifact against schema",
    )
    validate_parser.add_argument(
        "artifact",
        type=Path,
        help="Artifact file (YAML/JSON)",
    )
    validate_parser.add_argument(
        "--type",
        required=True,
        choices=["problem", "outcome", "specification", "evidence"],
        help="Artifact type",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "init":
            return init_command(args)
        if args.command == "run":
            return run_command(args)
        if args.command == "run-simulation":
            return run_simulation_command(args)
        if args.command == "measure-outcome":
            return measure_outcome_command(args)
        if args.command == "eval":
            return eval_command(args)
        if args.command == "review":
            return review_command(args)
        if args.command == "status":
            return status_command(args)
        if args.command == "list":
            return list_command(args)
        if args.command == "show":
            return show_command(args)
        if args.command == "validate":
            return validate_command(args)

        parser.print_help()
        return 1

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

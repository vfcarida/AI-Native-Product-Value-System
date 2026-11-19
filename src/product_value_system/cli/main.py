"""
CLI entry point for Product Value System.
"""

import argparse
import sys
from pathlib import Path

from product_value_system.__version__ import __version__
from product_value_system.cli.commands import (
    init_command,
    list_command,
    run_command,
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

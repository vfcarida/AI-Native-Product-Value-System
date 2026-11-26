"""
CLI entry point for Product Value System.
"""

import argparse
import sys
from pathlib import Path

from product_value_system.__version__ import __version__
from product_value_system.cli.commands import (
    adr_command,
    drift_command,
    eval_command,
    evidence_command,
    experiment_command,
    flags_command,
    init_command,
    interview_command,
    list_command,
    measure_outcome_command,
    review_command,
    run_command,
    run_simulation_command,
    show_command,
    spec_command,
    status_command,
    validate_command,
)
from product_value_system.cli.commands_p2 import (
    brownfield_command,
    devx_command,
    doctor_command,
    governance_command,
    register_p2_subparsers,
    route_command,
    serve_command,
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
    run_parser.add_argument(
        "--provider",
        choices=["auto", "openai", "anthropic", "gemini", "azure_openai", "ollama", "mock"],
        default="auto",
        help="Model provider override (default: auto)",
    )
    run_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional model/deployment override for the selected provider",
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
        "--provider",
        choices=["auto", "openai", "anthropic", "gemini", "azure_openai", "ollama", "mock"],
        default="auto",
        help="Model provider override (default: auto)",
    )
    eval_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional model/deployment override for the selected provider",
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
    review_parser.add_argument(
        "--provider",
        choices=["auto", "openai", "anthropic", "gemini", "azure_openai", "ollama", "mock"],
        default="auto",
        help="Model provider override (default: auto)",
    )
    review_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Optional model/deployment override for the selected provider",
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

    # pvs flags
    flags_parser = subparsers.add_parser(
        "flags",
        help="Manage feature flags and progressive rollouts (EVO-015)",
    )
    flags_parser.add_argument(
        "--file",
        type=Path,
        default=Path("flags.yaml"),
        help="Feature flags YAML file (default: flags.yaml)",
    )
    flags_sub = flags_parser.add_subparsers(dest="flags_action", help="Feature flag actions")

    flags_list = flags_sub.add_parser("list", help="List all configured feature flags")
    flags_list.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Feature flags YAML file (default: flags.yaml)",
    )

    flags_toggle = flags_sub.add_parser("toggle", help="Toggle a feature flag on/off")
    flags_toggle.add_argument("name", help="Feature flag name")
    flags_toggle.add_argument("--actor", default="cli", help="Audit actor identifier")
    flags_toggle.add_argument("--file", type=Path, default=None, help="Feature flags YAML file")

    flags_rollout = flags_sub.add_parser("rollout", help="Update rollout percentage or stage")
    flags_rollout.add_argument("name", help="Feature flag name")
    flags_rollout.add_argument(
        "--percentage",
        type=float,
        default=None,
        help="Rollout percentage (0.0 to 1.0)",
    )
    flags_rollout.add_argument(
        "--stage",
        choices=["dark", "canary", "staged_5", "staged_25", "staged_50", "full"],
        default=None,
        help="Rollout stage",
    )
    flags_rollout.add_argument("--actor", default="cli", help="Audit actor identifier")
    flags_rollout.add_argument("--file", type=Path, default=None, help="Feature flags YAML file")

    flags_eval = flags_sub.add_parser("evaluate", help="Evaluate a flag for a user/environment")
    flags_eval.add_argument("name", help="Feature flag name")
    flags_eval.add_argument("--user", default=None, help="User ID for consistent hashing")
    flags_eval.add_argument("--env", default=None, help="Environment name (e.g., staging)")
    flags_eval.add_argument("--file", type=Path, default=None, help="Feature flags YAML file")

    flags_advance = flags_sub.add_parser(
        "advance",
        help="Advance a flag to the next rollout stage after checking SLO quality gates",
    )
    flags_advance.add_argument("name", help="Feature flag name")
    flags_advance.add_argument("--file", type=Path, default=None, help="Feature flags YAML file")

    # pvs experiment
    exp_parser = subparsers.add_parser(
        "experiment",
        help="Design and analyze statistically rigorous A/B experiments (EVO-016)",
    )
    exp_sub = exp_parser.add_subparsers(dest="experiment_action", help="Experiment actions")

    exp_design = exp_sub.add_parser(
        "design",
        help="Generate an A/B experiment design from an outcome hypothesis YAML/JSON",
    )
    exp_design.add_argument("outcome", type=Path, help="Outcome hypothesis file (YAML or JSON)")
    exp_design.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output YAML/JSON path for experiment design",
    )
    exp_design.add_argument(
        "--daily-traffic",
        type=int,
        default=1000,
        help="Expected total daily users (default: 1000)",
    )
    exp_design.add_argument(
        "--confidence",
        type=float,
        default=0.95,
        help="Confidence level 1 - alpha (default: 0.95)",
    )
    exp_design.add_argument(
        "--power",
        type=float,
        default=0.80,
        help="Statistical power 1 - beta (default: 0.80)",
    )
    exp_design.add_argument(
        "--test-type",
        choices=["ttest", "chi_square", "bayesian"],
        default="ttest",
        help="Primary statistical test type (default: ttest)",
    )

    exp_analyze = exp_sub.add_parser(
        "analyze",
        help="Analyze recorded A/B test observations and print recommendation",
    )
    exp_analyze.add_argument(
        "experiment_file",
        type=Path,
        help="Saved experiment state file (JSON or YAML)",
    )
    exp_analyze.add_argument(
        "--test-type",
        choices=["ttest", "chi_square", "bayesian"],
        default=None,
        help="Override statistical test (ttest, chi_square, bayesian)",
    )
    exp_analyze.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output JSON path for experiment result",
    )

    exp_size = exp_sub.add_parser(
        "sample-size",
        help="Compute required per-variant sample size via power analysis",
    )
    exp_size.add_argument("--baseline", type=float, required=True, help="Baseline metric value")
    exp_size.add_argument(
        "--mde",
        type=float,
        required=True,
        help="Minimum detectable relative lift (e.g., 0.10 for 10%%)",
    )
    exp_size.add_argument(
        "--metric-type",
        choices=["continuous", "proportion"],
        default="continuous",
        help="Metric distribution type (default: continuous)",
    )
    exp_size.add_argument("--confidence", type=float, default=0.95, help="Confidence level")
    exp_size.add_argument("--power", type=float, default=0.80, help="Statistical power")
    exp_size.add_argument("--daily-traffic", type=int, default=1000, help="Expected daily traffic")

    exp_validate = exp_sub.add_parser(
        "validate-baseline",
        help="Validate baseline statistical quality, power, and stability (EVO-019)",
    )
    exp_validate.add_argument(
        "baseline_file",
        nargs="?",
        type=Path,
        default=None,
        help="Path to baseline JSON/YAML file",
    )
    exp_validate.add_argument(
        "--traces",
        type=Path,
        default=None,
        help="OpenTelemetry trace JSON/JSONL file to extract baseline from",
    )
    exp_validate.add_argument(
        "--metric",
        default="latency_ms",
        help="Metric name when extracting from traces (default: latency_ms)",
    )
    exp_validate.add_argument("--start", default=None, help="Optional ISO start timestamp filter")
    exp_validate.add_argument("--end", default=None, help="Optional ISO end timestamp filter")
    exp_validate.add_argument(
        "--effect-size",
        type=float,
        default=10.0,
        help="Target detectable effect size in percent (default: 10.0)",
    )
    exp_validate.add_argument(
        "--min-samples",
        type=int,
        default=30,
        help="Minimum required baseline sample size (default: 30)",
    )
    exp_validate.add_argument(
        "--max-cv",
        type=float,
        default=0.30,
        help="Maximum allowed coefficient of variation (default: 0.30)",
    )
    exp_validate.add_argument(
        "--target-power",
        type=float,
        default=0.80,
        help="Target statistical power (default: 0.80)",
    )
    exp_validate.add_argument(
        "--non-strict",
        action="store_true",
        default=False,
        help="Allow proceeding with warnings if no hard statistical errors exist",
    )
    exp_validate.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON output path for validation gate result",
    )

    # pvs drift
    drift_parser = subparsers.add_parser(
        "drift",
        help="Detect specification, API, and behavioral trace drift (EVO-017)",
    )
    drift_sub = drift_parser.add_subparsers(dest="drift_action", help="Drift actions")

    drift_check = drift_sub.add_parser(
        "check",
        help="Scan code vs specifications (and optional traces) for drift",
    )
    drift_check.add_argument("--code", nargs="+", default=["src/"], help="Code path(s) to scan")
    drift_check.add_argument("--spec", nargs="+", default=["docs/"], help="Spec path(s) to scan")
    drift_check.add_argument(
        "--severity",
        default="critical,major,moderate,minor",
        help="Comma-separated severities to check (default: all)",
    )
    drift_check.add_argument(
        "--include-behavior",
        action="store_true",
        default=False,
        help="Compare OpenTelemetry traces against behavioral SLA specifications",
    )
    drift_check.add_argument(
        "--traces",
        nargs="+",
        default=None,
        help="OpenTelemetry trace JSON/JSONL file(s) or directory",
    )
    drift_check.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Optional brownfield baseline JSON snapshot to suppress known drift",
    )
    drift_check.add_argument(
        "--save-baseline",
        type=Path,
        default=None,
        help="Save current findings as a baseline JSON snapshot",
    )
    drift_check.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional Markdown report output path",
    )
    drift_check.add_argument(
        "--json-report",
        type=Path,
        default=None,
        help="Optional JSON report output path",
    )
    drift_check.add_argument(
        "--max-major",
        type=int,
        default=3,
        help="Maximum allowed MAJOR findings before failing gate (default: 3)",
    )

    drift_report = drift_sub.add_parser(
        "report",
        help="Generate Markdown or JSON drift report (supports weekly digest)",
    )
    drift_report.add_argument("--code", nargs="+", default=["src/"], help="Code path(s) to scan")
    drift_report.add_argument("--spec", nargs="+", default=["docs/"], help="Spec path(s) to scan")
    drift_report.add_argument(
        "--output",
        type=Path,
        default=Path("drift_report.md"),
        help="Report output path (default: drift_report.md)",
    )
    drift_report.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Report output format (default: markdown)",
    )
    drift_report.add_argument(
        "--since",
        default=None,
        help="Optional reporting window label (e.g., '7 days ago')",
    )
    drift_report.add_argument(
        "--include-behavior",
        action="store_true",
        default=False,
        help="Include behavioral OpenTelemetry trace comparison",
    )
    drift_report.add_argument("--traces", nargs="+", default=None, help="Trace path(s)")

    drift_hook = drift_sub.add_parser(
        "install-hook",
        help="Install Git pre-commit drift detection hook",
    )
    drift_hook.add_argument(
        "--hooks-dir",
        type=Path,
        default=Path(".git/hooks"),
        help="Target Git hooks directory (default: .git/hooks)",
    )

    # pvs adr
    adr_parser = subparsers.add_parser(
        "adr",
        help="Manage Architecture Decision Records (EVO-018)",
    )
    adr_parser.add_argument(
        "--dir",
        dest="adr_dir",
        type=Path,
        default=Path("docs/adr"),
        help="ADR directory (default: docs/adr)",
    )
    adr_sub = adr_parser.add_subparsers(dest="adr_action", help="ADR actions")

    adr_new = adr_sub.add_parser("new", help="Create a new auto-numbered ADR")
    adr_new.add_argument("title", help="ADR title")
    adr_new.add_argument("--author", default="unknown", help="ADR author(s), comma-separated")
    adr_new.add_argument(
        "--status",
        choices=["proposed", "accepted", "deprecated", "superseded"],
        default="proposed",
        help="Initial status (default: proposed)",
    )
    adr_new.add_argument("--context", default="", help="Optional initial context")
    adr_new.add_argument("--decision", default="", help="Optional initial decision")
    adr_new.add_argument("--req", action="append", default=[], help="Related requirement ID")
    adr_new.add_argument("--code", action="append", default=[], help="Related code path")
    adr_new.add_argument("--tag", action="append", default=[], help="ADR tag")
    adr_new.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_list = adr_sub.add_parser("list", help="List ADRs with optional filtering")
    adr_list.add_argument(
        "--status",
        choices=["proposed", "accepted", "deprecated", "superseded"],
        default=None,
        help="Filter by status",
    )
    adr_list.add_argument("--tag", default=None, help="Filter by tag")
    adr_list.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    adr_list.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_show = adr_sub.add_parser("show", help="Display an ADR by ID")
    adr_show.add_argument("adr_id", help="ADR ID (e.g., ADR-0001)")
    adr_show.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_search = adr_sub.add_parser("search", help="Search ADRs by keyword")
    adr_search.add_argument("query", help="Search keyword")
    adr_search.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_supersede = adr_sub.add_parser(
        "supersede",
        help="Mark an existing ADR as superseded by a newer ADR",
    )
    adr_supersede.add_argument("old_adr_id", help="ID of ADR being superseded")
    adr_supersede.add_argument("new_adr_id", help="ID of superseding ADR")
    adr_supersede.add_argument(
        "--dir", dest="adr_dir", type=Path, default=None, help="ADR directory"
    )

    adr_accept = adr_sub.add_parser("accept", help="Mark a proposed ADR as accepted")
    adr_accept.add_argument("adr_id", help="ADR ID to accept")
    adr_accept.add_argument("--reviewer", default=None, help="Approving reviewer handle")
    adr_accept.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_deprecate = adr_sub.add_parser("deprecate", help="Mark an ADR as deprecated")
    adr_deprecate.add_argument("adr_id", help="ADR ID to deprecate")
    adr_deprecate.add_argument(
        "--dir", dest="adr_dir", type=Path, default=None, help="ADR directory"
    )

    adr_graph = adr_sub.add_parser(
        "graph",
        help="Print Mermaid decision graph visualizing ADR supersession links",
    )
    adr_graph.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_log = adr_sub.add_parser(
        "log",
        help="Generate Markdown decision log for team onboarding",
    )
    adr_log.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output Markdown path (prints to stdout if omitted)",
    )
    adr_log.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    adr_scan = adr_sub.add_parser(
        "scan-code",
        help="Scan source code comments for `# See ADR-XXXX` references",
    )
    adr_scan.add_argument("--code", nargs="+", default=["src/"], help="Source path(s) to scan")
    adr_scan.add_argument("--dir", dest="adr_dir", type=Path, default=None, help="ADR directory")

    # pvs spec
    spec_parser = subparsers.add_parser(
        "spec",
        help="Spec-Kit templates, validation, traceability, and export (EVO-020)",
    )
    spec_sub = spec_parser.add_subparsers(dest="spec_action", help="Spec-Kit actions")

    spec_new = spec_sub.add_parser("new", help="Generate a Spec-Kit specification from template")
    spec_new.add_argument(
        "template_type",
        choices=["user-story", "scenario", "api-contract", "nfr", "decision"],
        help="Template type to generate",
    )
    spec_new.add_argument("--title", required=True, help="Specification title or feature name")
    spec_new.add_argument(
        "--id",
        dest="spec_id",
        default=None,
        help="Optional explicit ID (e.g., US-042)",
    )
    spec_new.add_argument("--role", default="customer", help="User role for user stories")
    spec_new.add_argument("--goal", default=None, help="Goal statement for user stories")
    spec_new.add_argument("--benefit", default=None, help="Benefit statement for user stories")
    spec_new.add_argument("--method", default="POST", help="HTTP method for API contracts")
    spec_new.add_argument(
        "--path",
        dest="api_path",
        default=None,
        help="URL path for API contracts",
    )
    spec_new.add_argument("--category", default="performance", help="Category for NFRs")
    spec_new.add_argument(
        "--dir",
        dest="spec_dir",
        type=Path,
        default=Path("docs/specs"),
        help="Target specs directory (default: docs/specs)",
    )
    spec_new.add_argument("--output", type=Path, default=None, help="Explicit output Markdown path")

    spec_val = spec_sub.add_parser(
        "validate",
        help="Validate specification completeness and score (0-100)",
    )
    spec_val.add_argument("target", type=Path, help="Specification Markdown file or directory")
    spec_val.add_argument(
        "--type",
        dest="spec_type",
        default=None,
        help="Optional explicit spec type",
    )
    spec_val.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Optional minimum score threshold (0-100)",
    )

    spec_trace = spec_sub.add_parser(
        "trace",
        help="Find source code and test references for a specification ID",
    )
    spec_trace.add_argument("spec_id", help="Specification ID (e.g., US-042, SCENARIO-015)")
    spec_trace.add_argument("--root", type=Path, default=Path("."), help="Project root directory")

    spec_cov = spec_sub.add_parser(
        "coverage",
        help="Report spec-to-code and spec-to-test traceability coverage",
    )
    spec_cov.add_argument(
        "spec_dir",
        nargs="?",
        type=Path,
        default=Path("docs/specs"),
        help="Specifications directory (default: docs/specs)",
    )
    spec_cov.add_argument("--root", type=Path, default=Path("."), help="Project root directory")
    spec_cov.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    spec_export = spec_sub.add_parser(
        "export",
        help="Export consolidated Markdown catalog of specifications",
    )
    spec_export.add_argument(
        "spec_dir",
        nargs="?",
        type=Path,
        default=Path("docs/specs"),
        help="Specifications directory (default: docs/specs)",
    )
    spec_export.add_argument("--root", type=Path, default=Path("."), help="Project root directory")
    spec_export.add_argument(
        "--output",
        type=Path,
        default=Path("docs/specs/CATALOG.md"),
        help="Output Markdown catalog path",
    )

    # pvs interview (EVO-022: Customer Interview Synthesis)
    interview_parser = subparsers.add_parser(
        "interview",
        help="Customer interview synthesis, theme clustering, and export (EVO-022)",
    )
    interview_sub = interview_parser.add_subparsers(
        dest="interview_action",
        help="Interview synthesis operations",
    )

    iv_synth = interview_sub.add_parser(
        "synthesize",
        help="Synthesize insights from a single interview transcript (VTT, SRT, plain text)",
    )
    iv_synth.add_argument("transcript", type=Path, help="Path to interview transcript file")
    iv_synth.add_argument(
        "--interview-id",
        dest="interview_id",
        default=None,
        help="Optional explicit interview ID (defaults to file stem)",
    )
    iv_synth.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output file path for exported synthesis",
    )
    iv_synth.add_argument(
        "--format",
        choices=["json", "markdown", "notion"],
        default="json",
        help="Output export format (default: json)",
    )

    iv_batch = interview_sub.add_parser(
        "synthesize-batch",
        help="Batch-synthesize all transcripts in a directory",
    )
    iv_batch.add_argument("directory", type=Path, help="Directory of interview transcripts")
    iv_batch.add_argument(
        "--output",
        type=Path,
        default=Path("analysis"),
        help="Output directory for synthesized files (default: analysis/)",
    )
    iv_batch.add_argument(
        "--format",
        choices=["json", "markdown", "notion"],
        default="json",
        help="Output format for each synthesized file (default: json)",
    )

    iv_cluster = interview_sub.add_parser(
        "cluster-themes",
        help="Cluster semantically similar pain points across multiple synthesis JSON files",
    )
    iv_cluster.add_argument(
        "files",
        nargs="+",
        help="Synthesis JSON files, directories, or glob patterns",
    )
    iv_cluster.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Cosine similarity threshold for DBSCAN clustering (default: 0.7)",
    )
    iv_cluster.add_argument(
        "--min-samples",
        dest="min_samples",
        type=int,
        default=2,
        help="Minimum pain points required to form a cluster (default: 2)",
    )
    iv_cluster.add_argument(
        "--include-unclustered",
        action="store_true",
        default=False,
        help="Include singleton pain points as individual themes",
    )
    iv_cluster.add_argument(
        "--format",
        choices=["markdown", "json", "notion"],
        default="markdown",
        help="Report format (default: markdown)",
    )
    iv_cluster.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path for clustered theme report",
    )
    iv_cluster.add_argument(
        "--opportunity-map",
        dest="opportunity_map",
        type=Path,
        default=None,
        help="Optional JSON path to save populated Opportunity Map (EVO-005 integration)",
    )

    iv_export = interview_sub.add_parser(
        "export",
        help="Export a synthesized interview JSON file to JSON, Markdown, or Notion format",
    )
    iv_export.add_argument("synthesis_file", type=Path, help="Path to synthesis JSON file")
    iv_export.add_argument(
        "--format",
        choices=["json", "markdown", "notion"],
        default="markdown",
        help="Target export format (default: markdown)",
    )
    iv_export.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output file path (prints to stdout if omitted)",
    )

    iv_dash = interview_sub.add_parser(
        "dashboard",
        help="Aggregate insights over time and trending themes across synthesis JSON files",
    )
    iv_dash.add_argument(
        "targets",
        nargs="+",
        help="Synthesis JSON files, directories, or glob patterns",
    )
    iv_dash.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        help="Dashboard output format (default: markdown)",
    )

    # pvs evidence (EVO-023: Multi-Source Evidence Synthesis)
    evidence_parser = subparsers.add_parser(
        "evidence",
        help="Multi-source evidence synthesis, contradiction detection & network graph (EVO-023)",
    )
    evidence_sub = evidence_parser.add_subparsers(
        dest="evidence_action",
        help="Evidence synthesis operations",
    )

    ev_synth = evidence_sub.add_parser(
        "synthesize",
        help="Synthesize multi-source evidence (interviews, tickets, analytics, research, URLs)",
    )
    ev_synth.add_argument("topic", help="Research or product decision topic")
    ev_synth.add_argument(
        "--interviews",
        nargs="+",
        default=None,
        help="Interview synthesis JSON files, directories, or glob patterns",
    )
    ev_synth.add_argument(
        "--tickets",
        type=Path,
        default=None,
        help="Support tickets export file (JSON or CSV)",
    )
    ev_synth.add_argument(
        "--analytics",
        type=Path,
        default=None,
        help="Analytics metrics JSON file",
    )
    ev_synth.add_argument(
        "--research",
        nargs="+",
        default=None,
        help="Competitor research Markdown/text files or glob patterns",
    )
    ev_synth.add_argument(
        "--urls",
        nargs="+",
        default=None,
        help="External URL references",
    )
    ev_synth.add_argument(
        "--threshold",
        type=float,
        default=0.7,
        help="Semantic similarity threshold for contradiction detection (default: 0.7)",
    )
    ev_synth.add_argument(
        "--format",
        choices=["markdown", "json"],
        default=None,
        help="Report format (inferred from --output extension when omitted)",
    )
    ev_synth.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path for Markdown or JSON synthesis report",
    )
    ev_synth.add_argument(
        "--network-html",
        dest="network_html",
        type=Path,
        default=None,
        help="Optional output path for interactive HTML evidence network graph",
    )

    ev_vis = evidence_sub.add_parser(
        "visualize",
        help="Visualize claim-to-source evidence network from a synthesis JSON report",
    )
    ev_vis.add_argument("synthesis_file", type=Path, help="Path to synthesis JSON report")
    ev_vis.add_argument(
        "--format",
        choices=["html", "mermaid"],
        default="html",
        help="Visualization format (default: html)",
    )
    ev_vis.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path (default: evidence-network.html for html)",
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

    # Register modular Phase-2 & diagnostics subcommands (doctor, route, brownfield, devx)
    register_p2_subparsers(subparsers)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    command_handlers = {
        "init": init_command,
        "run": run_command,
        "run-simulation": run_simulation_command,
        "measure-outcome": measure_outcome_command,
        "eval": eval_command,
        "review": review_command,
        "flags": flags_command,
        "experiment": experiment_command,
        "drift": drift_command,
        "adr": adr_command,
        "spec": spec_command,
        "interview": interview_command,
        "evidence": evidence_command,
        "status": status_command,
        "list": list_command,
        "show": show_command,
        "validate": validate_command,
        "doctor": doctor_command,
        "route": route_command,
        "brownfield": brownfield_command,
        "devx": devx_command,
        "serve": serve_command,
        "governance": governance_command,
    }

    try:
        handler = command_handlers.get(args.command)
        if handler is not None:
            return handler(args)
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

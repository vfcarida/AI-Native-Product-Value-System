"""
Modular Phase-2 & Diagnostics CLI subcommands following clig.dev guidelines (Round 13):
- ``pvs doctor``: Environment, 12 YAML config contracts, Cedar policy & schema health checks
- ``pvs route``: Complexity-aware LLM model routing decision inspector (EVO-026)
- ``pvs brownfield``: Git history hotspots + optional Strangler Fig boundary plan (EVO-030/031)
- ``pvs devx``: Privacy-preserving SPACE / DORA DevX configuration & snapshot inspector (EVO-032)

All subcommands support ``--json`` machine-readable stdout output and semantic exit codes:
  0 = success, 1 = general error, 6 = health/diagnostic check failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from product_value_system.adapters.model_adapter import Message, Role
from product_value_system.brownfield import BoundaryAnalyzer, GitAnalyzer
from product_value_system.metrics import DevXTracker, SPACECalculator
from product_value_system.observability.structured_logging import run_system_diagnostics
from product_value_system.routing import ModelRouter

EXIT_OK = 0
EXIT_HEALTH_FAILURE = 6


def register_p2_subparsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``doctor``, ``route``, ``brownfield``, and ``devx`` subcommands."""
    # 1. pvs doctor
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run environment, YAML config, Cedar policy, and JSON schema diagnostics",
    )
    doctor_parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Repository root directory to inspect (default: current directory)",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON diagnostics to stdout",
    )

    # 2. pvs route
    route_parser = subparsers.add_parser(
        "route",
        help="Classify prompt complexity and inspect FinOps model routing decision (EVO-026)",
    )
    route_parser.add_argument("--prompt", required=True, help="Prompt text to classify and route")
    route_parser.add_argument(
        "--task-type",
        default=None,
        help="Optional task type hint (e.g. format_conversion, code_review, architecture_design)",
    )
    route_parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/routing.yaml"),
        help="Path to routing YAML configuration",
    )
    route_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON routing decision",
    )

    # 3. pvs brownfield
    bf_parser = subparsers.add_parser(
        "brownfield",
        help="Analyze git hotspots & optional Strangler Fig boundaries (EVO-030/031)",
    )
    bf_parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        help="Path to git repository to analyze (default: current directory)",
    )
    bf_parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Git history lookback window in days (default: 90)",
    )
    bf_parser.add_argument(
        "--strangler",
        action="store_true",
        help="Include Strangler Fig module boundary & modularity Q analysis",
    )
    bf_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON report",
    )

    # 4. pvs devx
    devx_parser = subparsers.add_parser(
        "devx",
        help="Inspect SPACE/DORA DevX tracking configuration & privacy posture (EVO-032)",
    )
    devx_parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/metrics.yaml"),
        help="Path to DevX metrics YAML configuration",
    )
    devx_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON snapshot",
    )

    # 5. pvs serve (Production ASGI Control Plane server — EVO-P3-01)
    serve_parser = subparsers.add_parser(
        "serve",
        help="Run the production ASGI Control Plane, Health, Metrics, Audit & DevX API server",
    )
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    serve_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of Uvicorn worker processes (default: 1)",
    )
    serve_parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for local development",
    )

    # 6. pvs governance export (EU AI Act + NIST AI RMF Governance Pack — EVO-P3-20)
    gov_parser = subparsers.add_parser(
        "governance",
        help="Generate EU AI Act + NIST AI RMF compliance & risk audit packs (EVO-P3-20)",
    )
    gov_sub = gov_parser.add_subparsers(dest="governance_action")
    gov_export = gov_sub.add_parser(
        "export",
        help="Export quarterly AI Governance Audit Pack (JSON or Markdown)",
    )
    gov_export.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Output format (default: json)",
    )
    gov_export.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional file path to write the governance pack (defaults to stdout)",
    )
    gov_export.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Repository root directory to inspect (default: current directory)",
    )
    gov_export.add_argument(
        "--include-redteam",
        action="store_true",
        help="Execute live OWASP ASI01-ASI10 red-team gate and embed results",
    )

    # 7. pvs ops (Executable PROD-015 GameDay, DR Drill & DORA AI Baseline — v3.0)
    ops_parser = subparsers.add_parser(
        "ops",
        help="Run executable PROD-015 Chaos GameDays, DR drills, and DORA AI baselines",
    )
    ops_sub = ops_parser.add_subparsers(dest="ops_action")

    gd_parser = ops_sub.add_parser(
        "gameday",
        help="Execute automated Chaos Engineering GameDay scenarios (GAMEDAY-001..004)",
    )
    gd_parser.add_argument(
        "--scenario",
        action="append",
        default=None,
        help="Specific GameDay scenario ID(s) to execute (defaults to all)",
    )
    gd_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Report format (default: json)",
    )
    gd_parser.add_argument("--output", type=Path, default=None, help="Optional output file path")

    dr_parser = ops_sub.add_parser(
        "dr-drill",
        help="Execute non-destructive backup/restore & HMAC audit integrity DR drill",
    )
    dr_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Report format (default: json)",
    )
    dr_parser.add_argument("--output", type=Path, default=None, help="Optional output file path")

    dora_parser = ops_sub.add_parser(
        "dora-baseline",
        help="Collect aggregate-only DORA AI 7-Capabilities & SPACE baseline snapshot",
    )
    dora_parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/metrics.yaml"),
        help="Path to DevX metrics YAML configuration",
    )
    dora_parser.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="json",
        help="Report format (default: json)",
    )
    dora_parser.add_argument("--output", type=Path, default=None, help="Optional output file path")


def doctor_command(args: argparse.Namespace) -> int:
    """Execute ``pvs doctor`` diagnostics."""
    report = run_system_diagnostics(args.repo_root)
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        status_banner = "HEALTHY" if report["healthy"] else "UNHEALTHY"
        print(f"PVS Doctor v{report['version']} — {status_banner}")
        for chk in report["checks"]:
            mark = "[OK]" if chk["ok"] else "[FAIL]"
            print(f"  {mark} {chk['name']}: {chk['detail']}")
    return EXIT_OK if report["healthy"] else EXIT_HEALTH_FAILURE


def route_command(args: argparse.Namespace) -> int:
    """Execute ``pvs route`` complexity classification & model selection."""
    cfg_path: Path = args.config
    router = ModelRouter.from_yaml(cfg_path) if cfg_path.is_file() else ModelRouter()
    messages = [Message(role=Role.USER, content=args.prompt)]
    decision = router.route(messages, task_type=args.task_type)
    payload = decision.to_dict()

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        tier = payload["complexity_level"]
        score = payload["complexity_score"]
        print(f"Complexity Tier : {tier} (score={score})")
        print(f"Selected Model  : {payload['provider']}/{payload['model']}")
        print(f"Est. Cost / 1K  : ${payload['estimated_cost_per_1k_tokens']:.6f}")
        print(f"Fallback Chain  : {', '.join(payload['fallback_models']) or 'none'}")
        print(f"Reasoning       : {payload['reasoning']}")
    return EXIT_OK


def brownfield_command(args: argparse.Namespace) -> int:
    """Execute ``pvs brownfield`` git hotspot & optional Strangler Fig analysis."""
    repo_path = Path(args.repo).resolve()
    lookback = max(1, int(args.days))
    analyzer = GitAnalyzer({"lookback_days": lookback, "max_commits_analyzed": 500})
    insights = asyncio.run(analyzer.analyze(repo_path))

    payload: dict[str, Any] = {
        "repo_path": str(repo_path),
        "days": lookback,
        "commits_analyzed": insights.total_commits,
        "analyzed_files": insights.analyzed_files,
        "hotspots": [h.to_dict() for h in insights.hotspots[:10]],
        "couplings": [c.to_dict() for c in insights.couplings[:10]],
    }

    if args.strangler:
        py_files = [
            p
            for p in repo_path.rglob("*.py")
            if ".git" not in p.parts and "__pycache__" not in p.parts
        ]
        code_structure: dict[str, dict[str, Any]] = {}
        for py_file in py_files[:500]:
            rel = py_file.relative_to(repo_path).as_posix()
            loc = len(py_file.read_text(encoding="utf-8", errors="ignore").splitlines())
            code_structure[rel] = {"loc": loc, "complexity": 3.0}
        boundary_analyzer = BoundaryAnalyzer()
        modules = boundary_analyzer.identify_modules(code_structure, repo_path=repo_path)
        payload["strangler_modules"] = [
            {
                "name": m.name,
                "file_count": len(m.file_paths),
                "lines_of_code": m.lines_of_code,
                "coupling_score": m.coupling_score,
                "complexity_score": m.complexity_score,
            }
            for m in modules[:10]
        ]

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Brownfield Analysis: {repo_path} (last {lookback} days)")
        print(f"  Commits analyzed : {payload['commits_analyzed']}")
        print(f"  Files analyzed   : {payload['analyzed_files']}")
        print(f"  Hotspots found   : {len(payload['hotspots'])}")
        for item in payload["hotspots"][:5]:
            print(
                f"    - {item['file_path']} (score={item['hotspot_score']:.2f}, "
                f"commits={item['commit_count']})"
            )
        if args.strangler:
            print(f"  Identified modules: {len(payload.get('strangler_modules', []))}")
            for mod in payload.get("strangler_modules", [])[:5]:
                print(
                    f"    - {mod['name']} ({mod['file_count']} files, "
                    f"coupling={mod['coupling_score']:.2f})"
                )
    return EXIT_OK


def devx_command(args: argparse.Namespace) -> int:
    """Execute ``pvs devx`` privacy-preserving SPACE/DORA snapshot."""
    from prometheus_client import CollectorRegistry

    cfg_path: Path = args.config
    reg = CollectorRegistry()
    tracker = (
        DevXTracker.from_yaml(cfg_path, registry=reg)
        if cfg_path.is_file()
        else DevXTracker({"enabled": True}, registry=reg)
    )
    calc = SPACECalculator(tracker=tracker)
    space_scores = asyncio.run(calc.compute_space_scores())
    dora_dict = tracker.get_dora_metrics().to_dict()
    payload: dict[str, Any] = {
        "enabled": tracker.enabled,
        "aggregate_only": tracker.aggregate_only,
        "anonymize": tracker.anonymize,
        "space_scores": space_scores,
        "dora_metrics": dora_dict,
        "ai_effectiveness": tracker.get_ai_effectiveness(),
    }

    if args.as_json:
        print(json.dumps(payload, indent=2))
    else:
        print("DevX SPACE & DORA Posture (Non-Punitive Aggregate Telemetry)")
        print(f"  Enabled        : {payload['enabled']}")
        print(f"  Aggregate Only : {payload['aggregate_only']}")
        print(f"  Anonymize PII  : {payload['anonymize']}")
        print(f"  SPACE Overall  : {space_scores.get('overall', 0.0):.2f}")
        print(f"  DORA Tier      : {dora_dict.get('performance_tier', 'unknown')}")
    return EXIT_OK


def serve_command(args: argparse.Namespace) -> int:
    """Execute ``pvs serve`` to launch the production ASGI Control Plane server."""
    import sys

    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn is required to run 'pvs serve'. "
            "Install with: pip install 'product-value-system[monitoring]'",
            file=sys.stderr,
        )
        return 1

    uvicorn.run(
        "product_value_system.api.app_factory:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=args.reload,
    )
    return EXIT_OK


def governance_command(args: argparse.Namespace) -> int:
    """Execute ``pvs governance export`` for EU AI Act & NIST AI RMF packs."""
    import sys

    action = getattr(args, "governance_action", None)
    if action != "export":
        print(
            "Usage: pvs governance export [--format json|markdown] [--output PATH]",
            file=sys.stderr,
        )
        return 1

    from product_value_system.compliance.ai_governance import (
        EnterpriseGovernancePackGenerator,
    )
    from product_value_system.security.agentic_guardrails import AgenticRedTeamRunner

    async def _run() -> str:
        rt_report = (
            await AgenticRedTeamRunner().run_all()
            if getattr(args, "include_redteam", False)
            else None
        )
        gen = EnterpriseGovernancePackGenerator(repo_root=args.repo_root)
        pack = await gen.generate(redteam_report=rt_report)
        if args.format == "markdown":
            return pack.render_markdown()
        return json.dumps(pack.to_dict(), indent=2)

    rendered = asyncio.run(_run())
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Governance audit pack written to {args.output}")
    else:
        print(rendered)
    return EXIT_OK


def ops_command(args: argparse.Namespace) -> int:
    """Execute ``pvs ops {gameday|dr-drill|dora-baseline}`` operational readiness drills."""
    import sys

    action = getattr(args, "ops_action", None)
    if action not in {"gameday", "dr-drill", "dora-baseline"}:
        print(
            "Usage: pvs ops {gameday|dr-drill|dora-baseline} [--format json|markdown]",
            file=sys.stderr,
        )
        return 1

    from product_value_system.operations.gameday_runner import (
        DORAAIBaselineCollector,
        DRDrillVerifier,
        GameDayScenarioRunner,
    )

    async def _execute() -> tuple[bool, str]:
        fmt = getattr(args, "format", "json")
        if action == "gameday":
            runner = GameDayScenarioRunner()
            gd_report = await runner.run_suite(scenario_ids=getattr(args, "scenario", None))
            text = (
                gd_report.render_markdown()
                if fmt == "markdown"
                else json.dumps(gd_report.to_dict(), indent=2)
            )
            return gd_report.overall_passed, text
        if action == "dr-drill":
            dr_report = await DRDrillVerifier().execute_drill()
            text = (
                dr_report.render_markdown()
                if fmt == "markdown"
                else json.dumps(dr_report.to_dict(), indent=2)
            )
            return dr_report.passed, text
        dora_report = await DORAAIBaselineCollector().collect(
            config_path=getattr(args, "config", None)
        )
        text = (
            dora_report.render_markdown()
            if fmt == "markdown"
            else json.dumps(dora_report.to_dict(), indent=2)
        )
        return True, text

    passed, rendered = asyncio.run(_execute())
    output_path: Path | None = getattr(args, "output", None)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"Operational drill report written to {output_path}")
    else:
        print(rendered)
    return EXIT_OK if passed else EXIT_HEALTH_FAILURE

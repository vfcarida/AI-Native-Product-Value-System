"""
Brownfield modernization agent combining Git history temporal analysis (EVO-030)
with static code complexity analysis to produce prioritized refactoring roadmaps.
"""

from __future__ import annotations

import ast
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from product_value_system.agents.agent import AgentResult
from product_value_system.brownfield.coupling_analyzer import Coupling
from product_value_system.brownfield.git_analyzer import (
    GitAnalyzer,
    GitInsights,
    load_brownfield_config,
)
from product_value_system.brownfield.ownership_analyzer import Ownership

logger = logging.getLogger(__name__)

_BRANCH_KEYWORDS_RE = re.compile(r"\b(?:if|elif|else if|for|while|case|catch|except|&&|\|\|)\b")


@dataclass
class FileComplexity:
    """Static complexity metrics for a single source file."""

    file_path: str
    lines_of_code: int
    cyclomatic_complexity: int
    function_count: int
    class_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "lines_of_code": self.lines_of_code,
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "function_count": self.function_count,
            "class_count": self.class_count,
        }


@dataclass
class ModernizationPriority:
    """Combined hotspot-churn × static-complexity refactoring candidate."""

    file_path: str
    priority_rank: int
    priority_score: float
    change_count: int
    change_frequency_per_month: float
    cyclomatic_complexity: int
    lines_of_code: int
    bus_factor: int
    is_knowledge_silo: bool
    stability: str
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "priority_rank": self.priority_rank,
            "priority_score": round(self.priority_score, 3),
            "change_count": self.change_count,
            "change_frequency_per_month": round(self.change_frequency_per_month, 3),
            "cyclomatic_complexity": self.cyclomatic_complexity,
            "lines_of_code": self.lines_of_code,
            "bus_factor": self.bus_factor,
            "is_knowledge_silo": self.is_knowledge_silo,
            "stability": self.stability,
            "recommendation": self.recommendation,
        }


@dataclass
class ModernizationReport:
    """Complete brownfield modernization report merging git history & static code metrics."""

    repo_path: str
    git_insights: GitInsights
    static_complexity: dict[str, FileComplexity]
    refactor_priorities: list[ModernizationPriority] = field(default_factory=list)
    coupling_risks: list[Coupling] = field(default_factory=list)
    ownership_risks: list[Ownership] = field(default_factory=list)
    stable_files: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "summary": {
                "total_commits_analyzed": self.git_insights.total_commits,
                "analyzed_files": len(self.static_complexity),
                "hotspots_found": len(self.git_insights.hotspots),
                "coupling_pairs_found": len(self.coupling_risks),
                "knowledge_silos_found": len(self.ownership_risks),
                "stable_legacy_files": len(self.stable_files),
            },
            "refactor_priorities": [p.to_dict() for p in self.refactor_priorities],
            "coupling_risks": [c.to_dict() for c in self.coupling_risks],
            "ownership_risks": [o.to_dict() for o in self.ownership_risks],
            "stable_files": list(self.stable_files),
            "recommended_actions": list(self.recommended_actions),
            "git_insights": self.git_insights.to_dict(),
        }

    def to_markdown(self) -> str:
        """Render a concise Markdown modernization report (no raw emails/PII)."""
        lines = [
            "# Brownfield Modernization Report",
            "",
            f"- **Commits analyzed**: {self.git_insights.total_commits} "
            f"(last {self.git_insights.analysis_period_days} days)",
            f"- **Source files scanned**: {len(self.static_complexity)}",
            f"- **Change hotspots**: {len(self.git_insights.hotspots)}",
            f"- **Temporal coupling pairs**: {len(self.coupling_risks)}",
            f"- **Knowledge silos**: {len(self.ownership_risks)}",
            "",
            "## Top Refactor Priorities (Hotspot × Complexity)",
            "",
        ]
        if self.refactor_priorities:
            lines.append("| Rank | File | Score | Commits | Complexity | LOC | Bus Factor |")
            lines.append("|---:|---|---:|---:|---:|---:|---:|")
            for item in self.refactor_priorities[:10]:
                lines.append(
                    f"| {item.priority_rank} | `{item.file_path}` | {item.priority_score:.2f} | "
                    f"{item.change_count} | {item.cyclomatic_complexity} | "
                    f"{item.lines_of_code} | {item.bus_factor} |"
                )
        else:
            lines.append("_No high-churn refactoring priorities detected in the lookback window._")

        lines.extend(["", "## Recommended Actions", ""])
        for action in self.recommended_actions:
            lines.append(f"- {action}")
        return "\n".join(lines) + "\n"


class StaticComplexityAnalyzer:
    """Lightweight multi-language static structure & cyclomatic complexity scanner."""

    def analyze_file(self, root: Path, file_path: Path) -> FileComplexity:
        rel = file_path.relative_to(root).as_posix()
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return FileComplexity(rel, 0, 1, 0, 0)

        non_empty_lines = [ln for ln in text.splitlines() if ln.strip()]
        loc = len(non_empty_lines)

        if file_path.suffix.lower() == ".py":
            try:
                tree = ast.parse(text)
                branches = 1
                funcs = 0
                classes = 0
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        funcs += 1
                    elif isinstance(node, ast.ClassDef):
                        classes += 1
                    elif isinstance(
                        node,
                        (
                            ast.If,
                            ast.For,
                            ast.AsyncFor,
                            ast.While,
                            ast.ExceptHandler,
                            ast.With,
                            ast.AsyncWith,
                            ast.IfExp,
                            ast.Match,
                        ),
                    ):
                        branches += 1
                    elif isinstance(node, ast.BoolOp):
                        branches += max(0, len(node.values) - 1)
                return FileComplexity(rel, loc, branches, funcs, classes)
            except SyntaxError:
                pass

        # Generic branch-keyword heuristic for Java, JS, TS, Go, etc.
        branches = 1 + len(_BRANCH_KEYWORDS_RE.findall(text))
        funcs = len(re.findall(r"\b(?:def|function|func)\b|\)\s*\{", text))
        classes = len(re.findall(r"\b(?:class|interface|struct)\b", text))
        return FileComplexity(rel, loc, branches, funcs, classes)

    def analyze_repo(
        self,
        repo_path: Path,
        file_filter: Any | None = None,
    ) -> dict[str, FileComplexity]:
        """Scan repository source files and return ``rel_path -> FileComplexity``."""
        results: dict[str, FileComplexity] = {}
        for path in sorted(repo_path.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(repo_path).as_posix()
            if rel.startswith(".git/") or "/.git/" in rel:
                continue
            if file_filter is not None and not file_filter(rel):
                continue
            results[rel] = self.analyze_file(repo_path, path)
        return results


def merge_insights(
    repo_path: Path | str,
    git_insights: GitInsights,
    static_complexity: dict[str, FileComplexity],
) -> ModernizationReport:
    """Merge temporal git history insights with static complexity to rank refactor targets."""
    hotspot_map = {h.file_path: h for h in git_insights.hotspots}
    all_candidates = set(hotspot_map.keys()) | set(static_complexity.keys())

    scored: list[ModernizationPriority] = []
    for rel_path in sorted(all_candidates):
        hs = hotspot_map.get(rel_path)
        cx = static_complexity.get(rel_path)
        if hs is None and cx is None:
            continue

        change_count = hs.change_count if hs is not None else 0
        change_freq = hs.change_frequency if hs is not None else 0.0
        complexity = cx.cyclomatic_complexity if cx is not None else 1
        loc = cx.lines_of_code if cx is not None else 0

        own = git_insights.ownership.get(rel_path)
        bus_factor = own.bus_factor if own is not None else 1
        is_silo = own.is_knowledge_silo if own is not None else False

        age = git_insights.file_ages.get(rel_path)
        stability = age.stability if age is not None else "unknown"

        # Priority score combines churn (change_count) and static complexity,
        # prioritizing high-churn + high-complexity files over untouched stable files.
        if change_count > 0:
            silo_multiplier = 1.25 if is_silo else 1.0
            priority_score = float(change_count) * float(complexity) * silo_multiplier
            if is_silo:
                rec = (
                    "High-churn complex hotspot with single-author knowledge silo: "
                    "add characterization tests, pair-refactor, and split responsibilities."
                )
            else:
                rec = (
                    "High-churn complex hotspot: prioritize modular decomposition "
                    "and automated regression tests before further feature edits."
                )
            scored.append(
                ModernizationPriority(
                    file_path=rel_path,
                    priority_rank=0,
                    priority_score=priority_score,
                    change_count=change_count,
                    change_frequency_per_month=change_freq,
                    cyclomatic_complexity=complexity,
                    lines_of_code=loc,
                    bus_factor=bus_factor,
                    is_knowledge_silo=is_silo,
                    stability=stability,
                    recommendation=rec,
                )
            )

    scored.sort(key=lambda p: (-p.priority_score, -p.change_count, p.file_path))
    for idx, item in enumerate(scored):
        item.priority_rank = idx + 1

    ownership_risks = sorted(
        (o for o in git_insights.ownership.values() if o.is_knowledge_silo),
        key=lambda o: (-o.primary_ownership_pct, o.file_path),
    )
    stable_files = sorted(
        path
        for path, age in git_insights.file_ages.items()
        if age.stability == "stable_legacy"
    )

    actions: list[str] = []
    if scored:
        top = scored[0]
        actions.append(
            f"Refactor priority #1: `{top.file_path}` "
            f"({top.change_count} commits, cyclomatic complexity {top.cyclomatic_complexity})."
        )
    if git_insights.couplings:
        top_c = git_insights.couplings[0]
        actions.append(
            f"Investigate hidden coupling between `{top_c.file1}` and `{top_c.file2}` "
            f"(coupling={top_c.coupling_coefficient:.2f}, {top_c.co_change_count} co-changes)."
        )
    if ownership_risks:
        actions.append(
            f"Mitigate bus-factor risk across {len(ownership_risks)} knowledge-silo file(s) "
            "via code review rotation and documentation."
        )
    if stable_files:
        actions.append(
            f"Defer refactoring on {len(stable_files)} stable legacy file(s) with low churn."
        )
    if not actions:
        actions.append("Codebase shows low churn; continue standard incremental modernization.")

    return ModernizationReport(
        repo_path=str(repo_path),
        git_insights=git_insights,
        static_complexity=static_complexity,
        refactor_priorities=scored,
        coupling_risks=list(git_insights.couplings),
        ownership_risks=ownership_risks,
        stable_files=stable_files,
        recommended_actions=actions,
    )


class BrownfieldAgent:
    """
    Brownfield codebase analysis agent integrating Git history temporal analysis
    with static complexity metrics to prioritize modernization work (EVO-030).
    """

    name = "brownfield-agent"

    def __init__(
        self,
        config: dict[str, Any] | Path | str | None = None,
        *,
        git_analyzer: GitAnalyzer | None = None,
        static_analyzer: StaticComplexityAnalyzer | None = None,
    ) -> None:
        if isinstance(config, (Path, str)):
            raw_cfg = load_brownfield_config(config)
        elif isinstance(config, dict):
            raw_cfg = dict(config)
        else:
            raw_cfg = {}

        self.git_analyzer = git_analyzer or GitAnalyzer(raw_cfg)
        self.static_analyzer = static_analyzer or StaticComplexityAnalyzer()

    async def analyze_repository(self, repo_path: Path | str) -> ModernizationReport:
        """Run git history analysis followed by static complexity analysis and merge."""
        target = Path(repo_path).resolve()

        # 1. Temporal Git history analysis (read-only)
        git_insights = await self.git_analyzer.analyze(target)

        # 2. Static code structure & complexity analysis
        static_complexity = self.static_analyzer.analyze_repo(
            target,
            file_filter=self.git_analyzer._matches_filters,
        )

        # 3. Merge into prioritized modernization report
        return merge_insights(target, git_insights, static_complexity)

    async def execute(
        self,
        input_artifact: dict[str, Any],
        harness: Any | None = None,
        context: Any | None = None,
    ) -> AgentResult:
        """Execute brownfield repository analysis adhering to the standard agent contract."""
        _ = harness, context
        start = time.monotonic()
        repo_raw = input_artifact.get("repo_path") or input_artifact.get("repository_path")
        if not repo_raw:
            return AgentResult(
                success=False,
                output_artifact={},
                cost_usd=0.0,
                iterations=1,
                duration_seconds=time.monotonic() - start,
                reasoning_trace=["Missing 'repo_path' in brownfield input artifact."],
                tool_calls=[],
                checkpoints=[],
                error="Missing 'repo_path' in input_artifact",
            )

        try:
            report = await self.analyze_repository(Path(str(repo_raw)))
            return AgentResult(
                success=True,
                output_artifact=report.to_dict(),
                cost_usd=0.0,
                iterations=1,
                duration_seconds=time.monotonic() - start,
                reasoning_trace=[
                    f"Analyzed {report.git_insights.total_commits} commits and "
                    f"{len(report.static_complexity)} source files.",
                    f"Identified {len(report.refactor_priorities)} refactor priorities and "
                    f"{len(report.coupling_risks)} coupled pairs.",
                ],
                tool_calls=[],
                checkpoints=[],
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                output_artifact={},
                cost_usd=0.0,
                iterations=1,
                duration_seconds=time.monotonic() - start,
                reasoning_trace=[f"Brownfield analysis failed: {exc}"],
                tool_calls=[],
                checkpoints=[],
                error=str(exc),
            )

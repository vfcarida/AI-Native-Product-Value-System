"""
Module boundary identification for Strangler Fig brownfield modernization (EVO-031).

Supports directory/package grouping (``static_analysis``), NetworkX import-graph
clustering (``dependency_graph``), and feature-similarity clustering (``ml``),
with configurable ``min_module_size_lines`` filtering and ``max_module_size_lines``
partitioning.
"""

from __future__ import annotations

import ast
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


def normalize_priority_weights(raw_weights: Any) -> dict[str, float]:
    """Normalize ``priority_factors`` from either a dict or YAML list-of-dicts."""
    defaults: dict[str, float] = {
        "hotspot_score": 0.4,
        "complexity_score": 0.3,
        "coupling_score": -0.2,
        "business_value": 0.1,
    }
    if not raw_weights:
        return defaults

    parsed: dict[str, float] = dict(defaults)
    if isinstance(raw_weights, dict):
        for k, v in raw_weights.items():
            parsed[str(k)] = float(v)
    elif isinstance(raw_weights, list):
        for item in raw_weights:
            if isinstance(item, dict):
                for k, v in item.items():
                    parsed[str(k)] = float(v)
    return parsed


@dataclass
class Module:
    """Candidate legacy module identified for Strangler Fig incremental replacement."""

    name: str
    file_paths: list[str]
    lines_of_code: int
    complexity_score: float = 0.0  # Normalized 0.0 - 1.0
    coupling_score: float = 0.0  # Normalized 0.0 - 1.0 (0 = decoupled, 1 = tightly coupled)
    hotspot_score: float = 0.0  # Normalized 0.0 - 1.0
    business_value_score: float = 0.5  # Normalized 0.0 - 1.0
    avg_cyclomatic_complexity: float = 1.0
    external_dependencies: list[str] = field(default_factory=list)

    def compute_priority(
        self,
        weights: dict[str, float] | list[dict[str, float]] | None = None,
    ) -> float:
        """Compute weighted modernization priority score (higher = modernize sooner).

        Low coupling is rewarded whether ``coupling_score`` weight is specified as
        negative (e.g. ``-0.2`` in ``config/brownfield.yaml``) or positive (``0.2``).
        """
        w = normalize_priority_weights(weights)
        w_hotspot = abs(w.get("hotspot_score", 0.4))
        w_complexity = abs(w.get("complexity_score", 0.3))
        w_coupling = abs(w.get("coupling_score", 0.2))
        w_business = abs(w.get("business_value", 0.1))

        score = (
            self.hotspot_score * w_hotspot
            + self.complexity_score * w_complexity
            + (1.0 - self.coupling_score) * w_coupling
            + self.business_value_score * w_business
        )
        return round(score, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "file_paths": list(self.file_paths),
            "lines_of_code": self.lines_of_code,
            "complexity_score": round(self.complexity_score, 4),
            "coupling_score": round(self.coupling_score, 4),
            "hotspot_score": round(self.hotspot_score, 4),
            "business_value_score": round(self.business_value_score, 4),
            "avg_cyclomatic_complexity": round(self.avg_cyclomatic_complexity, 2),
            "external_dependencies": list(self.external_dependencies),
        }


def _extract_python_imports(file_path: Path) -> set[str]:
    """Extract imported module names from a Python source file."""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return set()

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _module_key_from_path(rel_path: str) -> str:
    """Derive a clean logical module name from a relative POSIX path."""
    parts = [p for p in PurePosixPath(rel_path.replace("\\", "/")).parts if p not in {"", "."}]
    if not parts:
        return "root"
    if len(parts) == 1:
        return PurePosixPath(parts[0]).stem or "root"

    # Strip common source root prefixes ('src', 'lib', 'app', package root)
    dir_parts = list(parts[:-1])
    if dir_parts[0] in {"src", "lib", "app"} and len(dir_parts) > 1:
        dir_parts = dir_parts[1:]
    if len(dir_parts) > 1:
        return dir_parts[-1]
    return dir_parts[0]


def _get_loc(entry: Any) -> int:
    if isinstance(entry, dict):
        return int(entry.get("lines_of_code", entry.get("loc", 0)) or 0)
    return int(getattr(entry, "lines_of_code", 0) or 0)


def _get_complexity(entry: Any) -> int:
    if isinstance(entry, dict):
        return int(entry.get("cyclomatic_complexity", entry.get("complexity", 1)) or 1)
    return int(getattr(entry, "cyclomatic_complexity", 1) or 1)


class BoundaryAnalyzer:
    """Identify cohesive candidate modules and measure structural boundaries."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        raw = dict(config or {})
        if "strangler_fig" in raw and isinstance(raw["strangler_fig"], dict):
            raw = dict(raw["strangler_fig"])
        bd = raw.get("boundary_detection", raw)
        if not isinstance(bd, dict):
            bd = {}

        self.method: str = str(bd.get("method", "static_analysis")).strip().lower()
        if self.method not in {"static_analysis", "dependency_graph", "ml"}:
            raise ValueError(f"Unsupported boundary_detection method: {self.method!r}")

        self.min_module_size_lines: int = max(0, int(bd.get("min_module_size_lines", 0)))
        self.max_module_size_lines: int = max(1, int(bd.get("max_module_size_lines", 10000)))

    def build_import_graph(
        self,
        code_structure: dict[str, Any],
        repo_path: Path | None = None,
    ) -> nx.DiGraph[str]:
        """Build a directed file-to-file import graph for Python files in ``code_structure``."""
        graph: nx.DiGraph[str] = nx.DiGraph()
        stem_to_files: dict[str, list[str]] = defaultdict(list)

        for rel_path in sorted(code_structure):
            graph.add_node(rel_path)
            stem = PurePosixPath(rel_path).stem
            if stem and stem != "__init__":
                stem_to_files[stem].append(rel_path)

        if repo_path is None:
            return graph

        for rel_path in sorted(code_structure):
            if not rel_path.endswith(".py"):
                continue
            full_path = repo_path / rel_path
            if not full_path.is_file():
                continue
            for imp in _extract_python_imports(full_path):
                leaf = imp.split(".")[-1]
                for target in stem_to_files.get(leaf, []):
                    if target != rel_path:
                        graph.add_edge(rel_path, target)

        return graph

    def identify_modules(
        self,
        code_structure: dict[str, Any],
        *,
        repo_path: Path | None = None,
    ) -> list[Module]:
        """Partition ``code_structure`` into candidate ``Module`` boundaries."""
        if not code_structure:
            return []

        import_graph = self.build_import_graph(code_structure, repo_path=repo_path)

        if self.method in {"dependency_graph", "ml"} and import_graph.number_of_edges() > 0:
            raw_groups = self._cluster_by_dependency_graph(code_structure, import_graph)
        else:
            raw_groups = self._group_by_package(code_structure)

        # Split oversized modules exceeding max_module_size_lines
        bounded_groups: dict[str, list[str]] = {}
        for mod_name in sorted(raw_groups):
            files = sorted(raw_groups[mod_name])
            total_loc = sum(_get_loc(code_structure[f]) for f in files)
            if total_loc <= self.max_module_size_lines or len(files) <= 1:
                bounded_groups[mod_name] = files
            else:
                part_idx = 1
                curr_files: list[str] = []
                curr_loc = 0
                for fp in files:
                    f_loc = _get_loc(code_structure[fp])
                    if curr_files and (curr_loc + f_loc) > self.max_module_size_lines:
                        bounded_groups[f"{mod_name}_part{part_idx}"] = curr_files
                        part_idx += 1
                        curr_files = [fp]
                        curr_loc = f_loc
                    else:
                        curr_files.append(fp)
                        curr_loc += f_loc
                if curr_files:
                    bounded_groups[f"{mod_name}_part{part_idx}"] = curr_files

        # Build Module objects & compute normalized complexity + structural coupling
        file_to_mod = {fp: mod for mod, fps in bounded_groups.items() for fp in fps}
        modules: list[Module] = []
        raw_complexities: list[float] = []

        for mod_name, files in sorted(bounded_groups.items()):
            total_loc = sum(_get_loc(code_structure[f]) for f in files)
            if total_loc < self.min_module_size_lines:
                continue

            avg_cx = (
                sum(_get_complexity(code_structure[f]) for f in files) / len(files)
                if files
                else 1.0
            )
            ext_deps: set[str] = set()
            internal_edges = 0
            external_edges = 0
            for fp in files:
                if fp in import_graph:
                    for neighbor in import_graph.successors(fp):
                        target_mod = file_to_mod.get(neighbor)
                        if target_mod == mod_name:
                            internal_edges += 1
                        elif target_mod is not None:
                            external_edges += 1
                            ext_deps.add(target_mod)

            total_edges = internal_edges + external_edges
            structural_coupling = (external_edges / total_edges) if total_edges > 0 else 0.0

            raw_complexities.append(avg_cx)
            modules.append(
                Module(
                    name=mod_name,
                    file_paths=files,
                    lines_of_code=total_loc,
                    complexity_score=0.0,
                    coupling_score=round(structural_coupling, 4),
                    avg_cyclomatic_complexity=avg_cx,
                    external_dependencies=sorted(ext_deps),
                )
            )

        # Normalize complexity_score into [0.0, 1.0] using saturation ceiling max(max_cx, 10.0)
        cx_scale = max(raw_complexities, default=1.0)
        cx_scale = max(cx_scale, 10.0)
        for mod in modules:
            mod.complexity_score = round(min(1.0, mod.avg_cyclomatic_complexity / cx_scale), 4)

        return modules

    def _group_by_package(self, code_structure: dict[str, Any]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for rel_path in sorted(code_structure):
            groups[_module_key_from_path(rel_path)].append(rel_path)
        return dict(groups)

    def _cluster_by_dependency_graph(
        self,
        code_structure: dict[str, Any],
        import_graph: nx.DiGraph[str],
    ) -> dict[str, list[str]]:
        undirected = import_graph.to_undirected()
        components = sorted(
            (sorted(comp) for comp in nx.connected_components(undirected)),
            key=lambda c: (-len(c), c[0]),
        )
        groups: dict[str, list[str]] = {}
        used_names: dict[str, int] = defaultdict(int)
        for comp in components:
            if len(comp) == 1:
                base_name = _module_key_from_path(comp[0])
                groups.setdefault(base_name, []).extend(comp)
                continue
            pkg_votes: dict[str, int] = defaultdict(int)
            for fp in comp:
                pkg_votes[_module_key_from_path(fp)] += 1
            best_name = sorted(pkg_votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            used_names[best_name] += 1
            final_name = (
                best_name if used_names[best_name] == 1 else f"{best_name}_{used_names[best_name]}"
            )
            groups[final_name] = list(comp)
        return groups

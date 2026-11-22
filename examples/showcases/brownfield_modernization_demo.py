#!/usr/bin/env python3
"""
Showcase 2: Brownfield Modularity Detection & Shadow-Mode Strangler Fig ($0 API Cost)

Demonstrates EVO-031 + Round 2 algorithmic upgrades:
- Clauset-Newman-Moore greedy modularity community detection & Newman-Girvan Q
- Consistent SHA-256 progressive canary traffic routing (5% -> 25% -> 100%)
- Declarative Anti-Corruption Layer (ACL) generation with shadow-mode parity checks

Run:
    python examples/showcases/brownfield_modernization_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from product_value_system.brownfield.acl_generator import ACLGenerator  # noqa: E402
from product_value_system.brownfield.boundary_analyzer import (  # noqa: E402
    BoundaryAnalyzer,
    Module,
)
from product_value_system.brownfield.routing_strategy import should_route_to_modern  # noqa: E402


def run_demo() -> None:
    print("=== Showcase 2: Brownfield Modularity Q & Shadow-Mode Strangler Fig ===\n")

    # 1. Construct a bridged 3-domain legacy monolith graph
    graph: nx.DiGraph[str] = nx.DiGraph()
    for domain in ("billing", "orders", "identity"):
        nodes = [f"legacy/{domain}/svc_{i}.py" for i in range(6)]
        for i, src in enumerate(nodes):
            graph.add_node(src)
            graph.add_edge(src, nodes[(i + 1) % 6])
            graph.add_edge(src, nodes[(i + 2) % 6])
    graph.add_edge("legacy/orders/svc_0.py", "legacy/billing/svc_0.py")
    graph.add_edge("legacy/billing/svc_1.py", "legacy/identity/svc_0.py")

    analyzer = BoundaryAnalyzer()
    code_structure = {str(n): {"loc": 150, "complexity": 5.0} for n in graph.nodes}
    groups = analyzer._cluster_by_dependency_graph(code_structure, graph)
    modules = [
        Module(name=name, file_paths=files, lines_of_code=len(files) * 150)
        for name, files in groups.items()
    ]
    modularity_q = analyzer.compute_modularity(modules, graph)

    print(f"Detected {len(modules)} bounded contexts with Structural Modularity Q = {modularity_q}")
    for mod in modules:
        print(f"  - {mod.name:12s}: {len(mod.file_paths)} files ({mod.lines_of_code} LOC)")

    # 2. Progressive sticky canary routing
    users = [f"merchant-{i}" for i in range(100)]
    for pct in (5, 25, 100):
        routed = sum(1 for u in users if should_route_to_modern(u, pct))
        print(f"Canary {pct:3d}% -> {routed:3d}/100 merchants routed to modern service")

    # 3. Generate syntax-verified Anti-Corruption Layer with shadow-mode parity
    acl_gen = ACLGenerator()
    acl_source = acl_gen.generate(modules[0])
    compile(acl_source, "<strangler_acl>", "exec")
    print(
        f"\nGenerated ACL    : {len(acl_source.splitlines())} lines for '{modules[0].name}' "
        f"(syntax-verified, shadow_mode={'shadow_mode' in acl_source})"
    )


if __name__ == "__main__":
    run_demo()

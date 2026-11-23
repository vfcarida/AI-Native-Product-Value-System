#!/usr/bin/env python3
"""Capacity planning and FinOps cost projection model from k6 load test results (PROD-009)."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CapacityModel:
    """Empirically parameterized capacity and unit-economics model."""

    max_rps: float
    cost_per_1k_requests: float
    users_per_pod: int
    pods_per_node: int
    avg_tokens_per_request: float = 0.0
    monthly_node_cost_usd: float = 140.0  # e.g., 4 vCPU / 16 GiB Kubernetes worker node

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_rps": round(self.max_rps, 2),
            "cost_per_1k_requests_usd": round(self.cost_per_1k_requests, 6),
            "users_per_pod": self.users_per_pod,
            "pods_per_node": self.pods_per_node,
            "avg_tokens_per_request": round(self.avg_tokens_per_request, 1),
            "monthly_node_cost_usd": round(self.monthly_node_cost_usd, 2),
        }


def analyze_stress_test_results(
    results_path: Path | str,
    *,
    blended_price_per_1m_tokens_usd: float = 10.0,
    users_per_pod: int = 50,
    pods_per_node: int = 10,
    monthly_node_cost_usd: float = 140.0,
) -> CapacityModel:
    """Derive a ``CapacityModel`` from a k6 JSON summary export."""
    data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    metrics = data.get("metrics") or {}

    req_values = (metrics.get("http_reqs") or {}).get("values") or {}
    max_rps = float(req_values.get("rate", 0.0))
    total_requests = max(int(req_values.get("count", 0)), 1)
    total_tokens = float(((metrics.get("tokens_used") or {}).get("values") or {}).get("count", 0))

    avg_tokens = total_tokens / total_requests
    cost_per_1k = (avg_tokens / 1_000_000.0) * blended_price_per_1m_tokens_usd * 1000.0

    return CapacityModel(
        max_rps=max_rps,
        cost_per_1k_requests=cost_per_1k,
        users_per_pod=max(1, users_per_pod),
        pods_per_node=max(1, pods_per_node),
        avg_tokens_per_request=avg_tokens,
        monthly_node_cost_usd=monthly_node_cost_usd,
    )


def project_capacity(
    model: CapacityModel,
    target_users: int,
    *,
    active_user_ratio: float = 0.30,
    requests_per_active_user_per_day: int = 100,
    days_per_month: int = 30,
    ha_min_pods: int = 2,
) -> dict[str, Any]:
    """Project required Kubernetes pods, nodes, and monthly FinOps spend for ``target_users``."""
    if target_users < 1:
        raise ValueError("target_users must be >= 1")

    pods_needed = max(ha_min_pods, math.ceil(target_users / model.users_per_pod))
    nodes_needed = max(1, math.ceil(pods_needed / model.pods_per_node))

    requests_per_month = (
        target_users * active_user_ratio * requests_per_active_user_per_day * days_per_month
    )
    monthly_llm_cost = (requests_per_month / 1000.0) * model.cost_per_1k_requests
    monthly_infra_cost = nodes_needed * model.monthly_node_cost_usd
    monthly_total_cost = monthly_llm_cost + monthly_infra_cost

    return {
        "target_users": target_users,
        "pods_needed": pods_needed,
        "nodes_needed": nodes_needed,
        "requests_per_month": int(requests_per_month),
        "monthly_llm_cost_usd": round(monthly_llm_cost, 2),
        "monthly_infra_cost_usd": round(monthly_infra_cost, 2),
        "monthly_cost_usd": round(monthly_total_cost, 2),
        "cost_per_user_per_month": round(monthly_total_cost / target_users, 4),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project cluster capacity and monthly FinOps cost")
    parser.add_argument("summary_json", type=Path, help="Path to k6 JSON summary export")
    parser.add_argument(
        "--targets",
        nargs="+",
        type=int,
        default=[1000, 5000, 10000, 50000],
        help="Target user counts to project",
    )
    parser.add_argument("--users-per-pod", type=int, default=50)
    parser.add_argument("--pods-per-node", type=int, default=10)
    parser.add_argument("--token-price-per-1m", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(list(argv) if argv is not None else None)

    model = analyze_stress_test_results(
        args.summary_json,
        blended_price_per_1m_tokens_usd=args.token_price_per_1m,
        users_per_pod=args.users_per_pod,
        pods_per_node=args.pods_per_node,
    )
    projections = [project_capacity(model, t) for t in args.targets]

    if args.as_json:
        print(json.dumps({"model": model.to_dict(), "projections": projections}, indent=2))
        return 0

    print("Capacity Planning Model")
    print(f"  Peak throughput      : {model.max_rps:.1f} req/s")
    print(f"  Avg tokens / request : {model.avg_tokens_per_request:.1f}")
    print(f"  LLM cost / 1k reqs   : ${model.cost_per_1k_requests:.4f}")
    print(f"  Users / pod          : {model.users_per_pod}")
    print(f"  Pods / node          : {model.pods_per_node}")
    print("\nProjections:")
    for proj in projections:
        llm_usd = proj["monthly_llm_cost_usd"]
        infra_usd = proj["monthly_infra_cost_usd"]
        total_usd = proj["monthly_cost_usd"]
        per_user = proj["cost_per_user_per_month"]
        print(
            f"  {proj['target_users']:>6,} users -> {proj['pods_needed']:>4} pods, "
            f"{proj['nodes_needed']:>3} nodes | "
            f"LLM: ${llm_usd:,.2f} + Infra: ${infra_usd:,.2f} = "
            f"${total_usd:,.2f}/mo (${per_user:.4f}/user)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

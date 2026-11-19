"""
Shared fixtures, cost guardrails, and environment checks for real integration tests.
"""

import os
import shutil
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import docker
import pytest
import yaml


def _load_cost_limits() -> tuple[float, float]:
    """Load per-test and per-run cost limits from cost_limits.yaml."""
    limits_file = Path(__file__).parent / "cost_limits.yaml"
    if limits_file.exists():
        data = yaml.safe_load(limits_file.read_text(encoding="utf-8")) or {}
        return (
            float(data.get("max_cost_per_test", 0.05)),
            float(data.get("max_cost_per_run", 0.50)),
        )
    return 0.05, 0.50


@dataclass
class RealIntegrationCostTracker:
    """Tracks real LLM API spend across tests and enforces hard caps."""

    max_per_test: float = 0.05
    max_total: float = 0.50
    spent: float = 0.0

    def track(self, amount: float) -> None:
        """Record USD spend and abort immediately if total suite budget exceeded."""
        self.spent += amount
        if self.spent > self.max_total:
            raise RuntimeError(
                f"Test suite exceeded cost budget: "
                f"${self.spent:.4f} > ${self.max_total:.2f}"
            )


def real_tests_enabled() -> bool:
    """Return True when RUN_REAL_INTEGRATION_TESTS=1 is set."""
    return os.getenv("RUN_REAL_INTEGRATION_TESTS") == "1"


def docker_daemon_available() -> bool:
    """Return True if local Docker daemon responds to ping."""
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def cedar_cli_available() -> bool:
    """Return True if the Cedar CLI binary is installed on PATH."""
    if not shutil.which("cedar"):
        return False
    try:
        result = subprocess.run(
            ["cedar", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


@pytest.fixture(scope="session")
def cost_tracker() -> Generator[RealIntegrationCostTracker, None, None]:
    """Session-scoped cost tracker enforcing total suite cost cap."""
    max_per_test, max_total = _load_cost_limits()
    tracker = RealIntegrationCostTracker(
        max_per_test=max_per_test,
        max_total=max_total,
    )
    yield tracker
    print(f"\nTotal real integration test suite cost: ${tracker.spent:.4f}")


@pytest.fixture(autouse=True)
def cost_per_test(
    cost_tracker: RealIntegrationCostTracker,
    request: pytest.FixtureRequest,
) -> Generator[None, None, None]:
    """Enforce per-test USD cost cap."""
    initial = cost_tracker.spent
    yield
    test_cost = cost_tracker.spent - initial
    if test_cost > cost_tracker.max_per_test:
        pytest.fail(
            f"Test {request.node.name} exceeded per-test budget: "
            f"${test_cost:.4f} > ${cost_tracker.max_per_test:.2f}"
        )


@pytest.fixture(scope="session")
def schemas_dir() -> Path:
    """Path to repository JSON schemas directory."""
    return Path(__file__).parent.parent.parent / "schemas"


@pytest.fixture(scope="session")
def policies_dir() -> Path:
    """Path to repository Cedar policies directory."""
    return Path(__file__).parent.parent.parent / "policies"

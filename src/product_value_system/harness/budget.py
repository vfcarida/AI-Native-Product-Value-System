"""
Resource budget tracking and enforcement.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


@dataclass
class Budget:
    """
    Resource budget for agent execution.
    """

    max_cost_usd: Decimal | None = None
    max_iterations: int | None = None
    max_duration_seconds: int | None = None

    # Tracking
    current_cost_usd: Decimal = field(default_factory=lambda: Decimal("0.0"))
    current_iterations: int = 0
    start_time: datetime | None = None

    def start(self) -> None:
        """Start budget tracking."""
        self.start_time = datetime.now(UTC)

    def record_cost(self, cost_usd: Decimal) -> None:
        """Record cost expenditure."""
        self.current_cost_usd += cost_usd

    def record_iteration(self) -> None:
        """Record iteration."""
        self.current_iterations += 1

    def check_cost(self) -> bool:
        """Check if cost budget exceeded."""
        if self.max_cost_usd is None:
            return True
        return self.current_cost_usd <= self.max_cost_usd

    def check_iterations(self) -> bool:
        """Check if iteration budget exceeded."""
        if self.max_iterations is None:
            return True
        return self.current_iterations <= self.max_iterations

    def check_duration(self) -> bool:
        """Check if time budget exceeded."""
        if self.max_duration_seconds is None or self.start_time is None:
            return True

        elapsed = (datetime.now(UTC) - self.start_time).total_seconds()
        return elapsed <= self.max_duration_seconds

    def check_all(self) -> tuple[bool, str | None]:
        """
        Check all budget constraints.

        Returns:
            (within_budget, violation_message)
        """
        if not self.check_cost():
            return (
                False,
                f"Cost budget exceeded: ${self.current_cost_usd} > ${self.max_cost_usd}",
            )

        if not self.check_iterations():
            return (
                False,
                f"Iteration budget exceeded: {self.current_iterations} > {self.max_iterations}",
            )

        if not self.check_duration():
            assert self.start_time is not None
            elapsed = (datetime.now(UTC) - self.start_time).total_seconds()
            return (
                False,
                f"Time budget exceeded: {elapsed:.1f}s > {self.max_duration_seconds}s",
            )

        return True, None

    def remaining(self) -> dict[str, Any]:
        """Get remaining budget."""
        result: dict[str, Any] = {}

        if self.max_cost_usd is not None:
            result["cost_usd"] = float(self.max_cost_usd - self.current_cost_usd)

        if self.max_iterations is not None:
            result["iterations"] = self.max_iterations - self.current_iterations

        if self.max_duration_seconds is not None and self.start_time is not None:
            elapsed = (datetime.now(UTC) - self.start_time).total_seconds()
            result["duration_seconds"] = self.max_duration_seconds - elapsed

        return result


class BudgetExceededError(Exception):
    """Raised when budget constraint violated."""

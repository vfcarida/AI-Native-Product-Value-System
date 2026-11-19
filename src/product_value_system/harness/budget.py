"""
Resource budget tracking, hard enforcement guards, and emergency kill switches (EVO-007).
"""

import logging
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from opentelemetry import trace

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when a budget constraint is violated."""


class BudgetExhausted(BudgetExceededError):  # noqa: N818
    """Raised when per-run, daily, monthly, or token budget is exhausted."""


class EmergencyStopTriggered(Exception):  # noqa: N818
    """Raised when the global EMERGENCY_STOP kill switch is active."""


@dataclass
class Budget:
    """
    Resource budget tracker with hard enforcement guards.

    Features:
    - Per-run USD, token, iteration, and duration limits
    - Persistent daily and monthly cumulative USD tracking via SQLite
    - Warning alert + OpenTelemetry event + optional callback at 80% threshold
    - Emergency kill switch via ``EMERGENCY_STOP=1`` environment variable
    """

    max_cost_usd: Decimal | None = None
    max_iterations: int | None = None
    max_duration_seconds: int | None = None
    max_usd: float | None = None
    max_tokens: int | None = None
    daily_limit_usd: float | None = None
    monthly_limit_usd: float | None = None
    alert_threshold: float = 0.80
    alert_callback: Callable[[float, float, float], Any] | None = None
    db_path: Path | str | None = None
    emergency_stop_env_var: str = "EMERGENCY_STOP"

    # Current run tracking
    current_cost_usd: Decimal = field(default_factory=lambda: Decimal("0.0"))
    current_iterations: int = 0
    spent_tokens: int = 0
    start_time: datetime | None = None
    alerted: bool = False

    _resolved_db_path: Path | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # Keep max_usd and max_cost_usd synchronized for full backward compatibility
        if self.max_usd is not None and self.max_cost_usd is None:
            self.max_cost_usd = Decimal(str(self.max_usd))
        elif self.max_cost_usd is not None and self.max_usd is None:
            self.max_usd = float(self.max_cost_usd)

        # Initialize SQLite persistence whenever time-window limits or explicit db_path set
        if (
            self.db_path is not None
            or self.daily_limit_usd is not None
            or self.monthly_limit_usd is not None
        ):
            raw_path = self.db_path if self.db_path is not None else Path("~/.pvs/budget.db")
            self._resolved_db_path = Path(raw_path).expanduser()
            self._init_db()

    @classmethod
    def from_config_file(
        cls,
        config_path: Path | str = Path("config/budget_limits.yaml"),
        *,
        db_path: Path | str | None = None,
        alert_callback: Callable[[float, float, float], Any] | None = None,
    ) -> "Budget":
        """Load Budget defaults from YAML configuration file."""
        path = Path(config_path)
        if not path.exists():
            return cls(db_path=db_path, alert_callback=alert_callback)

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults = data.get("default_limits", {})
        alerts = data.get("alerts", {})
        emergency = data.get("emergency_stop", {})

        return cls(
            max_usd=defaults.get("per_run"),
            daily_limit_usd=defaults.get("daily"),
            monthly_limit_usd=defaults.get("monthly"),
            alert_threshold=float(alerts.get("threshold", 0.80)),
            alert_callback=alert_callback,
            db_path=db_path,
            emergency_stop_env_var=str(emergency.get("env_var", "EMERGENCY_STOP")),
        )

    @property
    def spent_usd(self) -> float:
        """Total USD spent in the current run."""
        return float(self.current_cost_usd)

    @spent_usd.setter
    def spent_usd(self, value: float) -> None:
        self.current_cost_usd = Decimal(str(value))

    def _init_db(self) -> None:
        """Initialize SQLite tables for daily/monthly cumulative spend."""
        if self._resolved_db_path is None:
            return
        try:
            self._resolved_db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self._resolved_db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_spend (
                        date TEXT PRIMARY KEY,
                        spent_usd REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS monthly_spend (
                        month TEXT PRIMARY KEY,
                        spent_usd REAL NOT NULL
                    )
                    """
                )
                conn.commit()
        except (sqlite3.Error, OSError) as exc:
            logger.warning("Budget DB initialization skipped (%s): %s", self._resolved_db_path, exc)

    def start(self) -> None:
        """Start budget tracking."""
        self.start_time = datetime.now(UTC)

    def check_before_spend(
        self,
        estimated_cost_usd: float = 0.0,
        estimated_tokens: int = 0,
    ) -> None:
        """
        Enforce emergency stop and budget limits BEFORE making an LLM call.

        Args:
            estimated_cost_usd: Projected USD cost of the upcoming call
            estimated_tokens: Projected token count of the upcoming call

        Raises:
            EmergencyStopTriggered: If ``EMERGENCY_STOP=1`` is active
            BudgetExhausted: If per-run, token, daily, or monthly budget would be exceeded
        """
        if os.getenv(self.emergency_stop_env_var) == "1":
            span = trace.get_current_span()
            span.add_event(
                name="budget.emergency_stop",
                attributes={"budget.env_var": self.emergency_stop_env_var},
            )
            logger.critical(
                "Emergency stop activated (%s=1). All agent operations halted.",
                self.emergency_stop_env_var,
            )
            raise EmergencyStopTriggered(
                f"Emergency stop activated ({self.emergency_stop_env_var}=1). "
                "All agent operations halted."
            )

        # Per-run USD budget check
        if self.max_usd is not None:
            projected = self.spent_usd + estimated_cost_usd
            if projected >= self.max_usd:
                raise BudgetExhausted(
                    f"Per-run budget exhausted: ${projected:.4f} >= ${self.max_usd:.2f}"
                )

        # Per-run token budget check
        if self.max_tokens is not None:
            projected_tokens = self.spent_tokens + estimated_tokens
            if projected_tokens >= self.max_tokens:
                raise BudgetExhausted(
                    f"Token budget exhausted: {projected_tokens} >= {self.max_tokens}"
                )

        # Daily cumulative USD budget check
        if self.daily_limit_usd is not None:
            daily_spent = self._get_daily_spend()
            projected_daily = daily_spent + estimated_cost_usd
            if projected_daily >= self.daily_limit_usd:
                raise BudgetExhausted(
                    f"Daily budget exhausted: ${projected_daily:.4f} >= ${self.daily_limit_usd:.2f}"
                )

        # Monthly cumulative USD budget check
        if self.monthly_limit_usd is not None:
            monthly_spent = self._get_monthly_spend()
            projected_monthly = monthly_spent + estimated_cost_usd
            if projected_monthly >= self.monthly_limit_usd:
                raise BudgetExhausted(
                    f"Monthly budget exhausted: "
                    f"${projected_monthly:.4f} >= ${self.monthly_limit_usd:.2f}"
                )

        # Alert threshold check (per-run)
        if self.max_usd is not None and self.max_usd > 0 and not self.alerted:
            projected_total = self.spent_usd + estimated_cost_usd
            usage_fraction = projected_total / self.max_usd
            if usage_fraction >= self.alert_threshold:
                self._trigger_alert(projected_total, self.max_usd, usage_fraction)

    def spend(self, cost_usd: float | Decimal, tokens: int = 0) -> None:
        """
        Record actual spending after an LLM call completes.

        Args:
            cost_usd: Actual cost in USD
            tokens: Total tokens consumed
        """
        cost_decimal = cost_usd if isinstance(cost_usd, Decimal) else Decimal(str(cost_usd))
        cost_float = float(cost_decimal)

        self.current_cost_usd += cost_decimal
        self.spent_tokens += tokens

        if self._resolved_db_path is not None and cost_float > 0:
            self._record_daily_spend(cost_float)
            self._record_monthly_spend(cost_float)

        if self.max_usd is not None and self.max_usd > 0 and not self.alerted:
            usage_fraction = self.spent_usd / self.max_usd
            if usage_fraction >= self.alert_threshold:
                self._trigger_alert(self.spent_usd, self.max_usd, usage_fraction)

    def record_cost(self, cost_usd: Decimal | float) -> None:
        """Record cost expenditure (legacy compatible)."""
        self.spend(cost_usd, tokens=0)

    def record_iteration(self) -> None:
        """Record iteration."""
        self.current_iterations += 1

    def is_exhausted(self) -> bool:
        """Check if per-run USD or token budget is exhausted."""
        if self.max_usd is not None and self.spent_usd >= self.max_usd:
            return True
        if self.max_tokens is not None and self.spent_tokens >= self.max_tokens:
            return True
        return False

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
        """Get remaining budget across active limits."""
        result: dict[str, Any] = {}

        if self.max_cost_usd is not None:
            result["cost_usd"] = float(self.max_cost_usd - self.current_cost_usd)

        if self.max_iterations is not None:
            result["iterations"] = self.max_iterations - self.current_iterations

        if self.max_tokens is not None:
            result["tokens"] = self.max_tokens - self.spent_tokens

        if self.daily_limit_usd is not None:
            result["daily_usd"] = self.daily_limit_usd - self._get_daily_spend()

        if self.monthly_limit_usd is not None:
            result["monthly_usd"] = self.monthly_limit_usd - self._get_monthly_spend()

        if self.max_duration_seconds is not None and self.start_time is not None:
            elapsed = (datetime.now(UTC) - self.start_time).total_seconds()
            result["duration_seconds"] = self.max_duration_seconds - elapsed

        return result

    def _get_daily_spend(self) -> float:
        """Get cumulative spending for today (UTC)."""
        if self._resolved_db_path is None:
            return 0.0
        today = datetime.now(UTC).date().isoformat()
        try:
            with sqlite3.connect(self._resolved_db_path) as conn:
                row = conn.execute(
                    "SELECT spent_usd FROM daily_spend WHERE date = ?",
                    (today,),
                ).fetchone()
                return float(row[0]) if row else 0.0
        except sqlite3.Error as exc:
            logger.warning("Failed reading daily spend: %s", exc)
            return 0.0

    def _get_monthly_spend(self) -> float:
        """Get cumulative spending for current month (UTC)."""
        if self._resolved_db_path is None:
            return 0.0
        month = datetime.now(UTC).strftime("%Y-%m")
        try:
            with sqlite3.connect(self._resolved_db_path) as conn:
                row = conn.execute(
                    "SELECT spent_usd FROM monthly_spend WHERE month = ?",
                    (month,),
                ).fetchone()
                return float(row[0]) if row else 0.0
        except sqlite3.Error as exc:
            logger.warning("Failed reading monthly spend: %s", exc)
            return 0.0

    def _record_daily_spend(self, cost_usd: float) -> None:
        """Persist daily cumulative spend to SQLite (degrades gracefully on DB error)."""
        if self._resolved_db_path is None:
            return
        today = datetime.now(UTC).date().isoformat()
        try:
            with sqlite3.connect(self._resolved_db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO daily_spend (date, spent_usd) VALUES (?, ?)
                    ON CONFLICT(date) DO UPDATE SET spent_usd = spent_usd + ?
                    """,
                    (today, cost_usd, cost_usd),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Failed persisting daily spend: %s", exc)

    def _record_monthly_spend(self, cost_usd: float) -> None:
        """Persist monthly cumulative spend to SQLite (degrades gracefully on DB error)."""
        if self._resolved_db_path is None:
            return
        month = datetime.now(UTC).strftime("%Y-%m")
        try:
            with sqlite3.connect(self._resolved_db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO monthly_spend (month, spent_usd) VALUES (?, ?)
                    ON CONFLICT(month) DO UPDATE SET spent_usd = spent_usd + ?
                    """,
                    (month, cost_usd, cost_usd),
                )
                conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Failed persisting monthly spend: %s", exc)

    def _trigger_alert(
        self,
        projected_or_spent_usd: float,
        max_budget_usd: float,
        usage_fraction: float,
    ) -> None:
        """Emit budget threshold warning, OpenTelemetry event, and callback."""
        span = trace.get_current_span()
        span.add_event(
            name="budget.alert",
            attributes={
                "budget.usage_fraction": round(usage_fraction, 4),
                "budget.spent_usd": round(projected_or_spent_usd, 6),
                "budget.limit_usd": round(max_budget_usd, 6),
            },
        )
        logger.warning(
            "Budget alert: %.1f%% of budget used ($%.4f / $%.2f)",
            usage_fraction * 100.0,
            projected_or_spent_usd,
            max_budget_usd,
        )
        if self.alert_callback is not None:
            self.alert_callback(projected_or_spent_usd, max_budget_usd, usage_fraction)
        self.alerted = True

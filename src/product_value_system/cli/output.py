"""
CLI output formatting utilities.
"""

import sys
from typing import Any


def _safe_print(text: str, file: Any = None) -> None:
    """Print text safely even on non-UTF-8 Windows consoles."""
    target = file or sys.stdout
    try:
        print(text, file=target)
    except UnicodeEncodeError:
        ascii_text = text.encode("ascii", errors="replace").decode("ascii")
        print(ascii_text, file=target)


def print_table(
    data: list[dict[str, Any]],
    headers: list[str] | None = None,
) -> None:
    """Print data as a table."""
    if not data:
        return

    resolved_headers = headers or list(data[0].keys())

    widths = {h: len(str(h)) for h in resolved_headers}
    for row in data:
        for header in resolved_headers:
            widths[header] = max(widths[header], len(str(row.get(header, ""))))

    header_row = " | ".join(str(h).ljust(widths[h]) for h in resolved_headers)
    _safe_print(header_row)
    _safe_print("-" * len(header_row))

    for row in data:
        _safe_print(
            " | ".join(str(row.get(h, "")).ljust(widths[h]) for h in resolved_headers)
        )


def print_status(run: Any) -> None:
    """Print run status."""
    status_symbol = {
        "pending": "[PENDING]",
        "running": "[RUNNING]",
        "waiting_approval": "[WAITING]",
        "completed": "[COMPLETED]",
        "failed": "[FAILED]",
        "cancelled": "[CANCELLED]",
    }.get(str(run.status), "[UNKNOWN]")

    _safe_print(f"{status_symbol} Run: {run.id}")
    _safe_print(f"   Status: {run.status}")
    _safe_print(f"   Problem: {run.problem_id}")
    _safe_print(f"   Cost: ${run.total_cost_usd:.2f}")


def print_error(message: str) -> None:
    """Print error message."""
    _safe_print(f"ERROR: {message}", file=sys.stderr)


def print_success(message: str) -> None:
    """Print success message."""
    _safe_print(f"OK: {message}")

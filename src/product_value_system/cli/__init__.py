"""
Command-line interface (CLI) for the AI-native Product Value System.

Uses lazy PEP 562 attribute loading so ``python -m product_value_system.cli.main``
executes without triggering Python's ``runpy`` double-import ``RuntimeWarning``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from product_value_system.cli.main import create_parser, main


def __getattr__(name: str) -> Any:
    if name in {"create_parser", "main"}:
        mod = importlib.import_module("product_value_system.cli.main")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_parser", "main"]

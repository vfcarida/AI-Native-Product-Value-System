"""
AI-Native Product Value System — Reference Implementation.

Exposes ergonomic, lazy-loaded top-level symbols for the primary control-plane
components without imposing upfront import overhead or circular dependencies.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from product_value_system.__version__ import __version__

if TYPE_CHECKING:
    from product_value_system.adapters import (
        AzureOpenAIModelAdapter,
        GeminiModelAdapter,
        OllamaModelAdapter,
        create_adapter,
    )
    from product_value_system.agents.brownfield_agent import BrownfieldAgent
    from product_value_system.brownfield.git_analyzer import GitAnalyzer
    from product_value_system.brownfield.strangler_planner import StranglerPlanner
    from product_value_system.caching.prompt_cache import PromptCache
    from product_value_system.contracts.config_schemas import validate_all_configs
    from product_value_system.evaluation.batch_runner import BatchEvalRunner
    from product_value_system.evaluation.eval_runner import EvalRunner
    from product_value_system.harness.harness import Harness
    from product_value_system.harness.resilience import CircuitBreaker
    from product_value_system.metrics.devx_tracker import DevXTracker
    from product_value_system.metrics.space_calculator import SPACECalculator
    from product_value_system.observability.structured_logging import run_system_diagnostics
    from product_value_system.orchestrator.controller import ValueStreamOrchestrator
    from product_value_system.orchestrator.parallel_executor import ParallelExecutor
    from product_value_system.persistence.postgres import PostgresDatabase, create_database
    from product_value_system.policy.engine import PolicyEngine
    from product_value_system.routing.model_router import ModelRouter
    from product_value_system.sandbox.network_filter import NetworkFilter
    from product_value_system.security.hybrid_detector import HybridSecretDetector

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AzureOpenAIModelAdapter": (
        "product_value_system.adapters.azure_openai_adapter",
        "AzureOpenAIModelAdapter",
    ),
    "BatchEvalRunner": ("product_value_system.evaluation.batch_runner", "BatchEvalRunner"),
    "BrownfieldAgent": ("product_value_system.agents.brownfield_agent", "BrownfieldAgent"),
    "CircuitBreaker": ("product_value_system.harness.resilience", "CircuitBreaker"),
    "DevXTracker": ("product_value_system.metrics.devx_tracker", "DevXTracker"),
    "EvalRunner": ("product_value_system.evaluation.eval_runner", "EvalRunner"),
    "GeminiModelAdapter": (
        "product_value_system.adapters.gemini_adapter",
        "GeminiModelAdapter",
    ),
    "GitAnalyzer": ("product_value_system.brownfield.git_analyzer", "GitAnalyzer"),
    "Harness": ("product_value_system.harness.harness", "Harness"),
    "HybridSecretDetector": (
        "product_value_system.security.hybrid_detector",
        "HybridSecretDetector",
    ),
    "ModelRouter": ("product_value_system.routing.model_router", "ModelRouter"),
    "NetworkFilter": ("product_value_system.sandbox.network_filter", "NetworkFilter"),
    "OllamaModelAdapter": (
        "product_value_system.adapters.ollama_adapter",
        "OllamaModelAdapter",
    ),
    "ParallelExecutor": (
        "product_value_system.orchestrator.parallel_executor",
        "ParallelExecutor",
    ),
    "PolicyEngine": ("product_value_system.policy.engine", "PolicyEngine"),
    "PostgresDatabase": ("product_value_system.persistence.postgres", "PostgresDatabase"),
    "PromptCache": ("product_value_system.caching.prompt_cache", "PromptCache"),
    "SPACECalculator": ("product_value_system.metrics.space_calculator", "SPACECalculator"),
    "StranglerPlanner": (
        "product_value_system.brownfield.strangler_planner",
        "StranglerPlanner",
    ),
    "ValueStreamOrchestrator": (
        "product_value_system.orchestrator.controller",
        "ValueStreamOrchestrator",
    ),
    "create_adapter": ("product_value_system.adapters", "create_adapter"),
    "create_database": ("product_value_system.persistence.postgres", "create_database"),
    "run_system_diagnostics": (
        "product_value_system.observability.structured_logging",
        "run_system_diagnostics",
    ),
    "validate_all_configs": (
        "product_value_system.contracts.config_schemas",
        "validate_all_configs",
    ),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        module_path, attr_name = _LAZY_EXPORTS[name]
        module = importlib.import_module(module_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AzureOpenAIModelAdapter",
    "BatchEvalRunner",
    "BrownfieldAgent",
    "CircuitBreaker",
    "DevXTracker",
    "EvalRunner",
    "GeminiModelAdapter",
    "GitAnalyzer",
    "Harness",
    "HybridSecretDetector",
    "ModelRouter",
    "NetworkFilter",
    "OllamaModelAdapter",
    "ParallelExecutor",
    "PolicyEngine",
    "PostgresDatabase",
    "PromptCache",
    "SPACECalculator",
    "StranglerPlanner",
    "ValueStreamOrchestrator",
    "__version__",
    "create_adapter",
    "create_database",
    "run_system_diagnostics",
    "validate_all_configs",
]

"""
Parallel multi-agent independent review orchestration (Elephant-Goldfish pattern, EVO-013).

Executes 4 specialized independent reviewers (Security, Performance, Maintainability,
Architecture) plus any custom reviewers concurrently via ``asyncio.gather``, each
with an isolated cleared-memory ``Harness``, and synthesizes findings and conflicts
into a single release gate decision.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from product_value_system.contracts.validation import SchemaValidator
from product_value_system.harness.budget import Budget
from product_value_system.harness.context import ExecutionContext
from product_value_system.harness.guardrails import Guardrails
from product_value_system.harness.harness import Harness
from product_value_system.policy.engine import PolicyEngine
from product_value_system.review.models import ReviewResult
from product_value_system.review.synthesizer import ReviewSynthesizer

if TYPE_CHECKING:
    from product_value_system.adapters.model_adapter import ModelAdapter

logger = logging.getLogger(__name__)


class MultiAgentReviewOrchestrator:
    """
    Runs independent specialized review agents in parallel with Goldfish
    isolation (fresh Harness + cleared Memory per reviewer) and synthesizes
    a consolidated ``ReviewResult``.
    """

    def __init__(
        self,
        schema_validator: SchemaValidator,
        *,
        harness_factory: Callable[[str], Harness] | None = None,
        model_adapter: ModelAdapter | None = None,
        policy_engine: PolicyEngine | None = None,
        synthesizer: ReviewSynthesizer | None = None,
        extra_reviewers: list[Any] | None = None,
        per_agent_budget_usd: float = 0.25,
        per_agent_timeout_seconds: float = 30.0,
    ) -> None:
        from product_value_system.agents.architecture_review_agent import ArchitectureReviewAgent
        from product_value_system.agents.maintainability_review_agent import (
            MaintainabilityReviewAgent,
        )
        from product_value_system.agents.performance_review_agent import PerformanceReviewAgent
        from product_value_system.agents.security_review_agent import SecurityReviewAgent

        self.schema_validator = schema_validator
        self.harness_factory = harness_factory
        self.model_adapter = model_adapter
        self.policy_engine = policy_engine
        self.synthesizer = synthesizer or ReviewSynthesizer()
        self.per_agent_budget_usd = per_agent_budget_usd
        self.per_agent_timeout_seconds = per_agent_timeout_seconds

        self.security_agent = SecurityReviewAgent(schema_validator)
        self.performance_agent = PerformanceReviewAgent(schema_validator)
        self.maintainability_agent = MaintainabilityReviewAgent(schema_validator)
        self.architecture_agent = ArchitectureReviewAgent(schema_validator)
        self.extra_reviewers: list[Any] = list(extra_reviewers or [])

    def _build_isolated_harness(
        self,
        reviewer_key: str,
        agent_name: str,
        run_id: str,
    ) -> Harness:
        """Create a fresh, cleared-memory Harness per reviewer (Goldfish isolation)."""
        if self.harness_factory is not None:
            harness = self.harness_factory(reviewer_key)
            harness.memory.clear()
            return harness

        ctx = ExecutionContext(
            run_id=run_id,
            stage_name=f"review_{reviewer_key}",
            agent_name=agent_name,
        )
        return Harness(
            context=ctx,
            policy_engine=self.policy_engine,
            budget=Budget(max_usd=self.per_agent_budget_usd),
            guardrails=Guardrails(max_iterations=3),
            model_adapter=self.model_adapter,
        )

    async def _run_single_reviewer(
        self,
        reviewer_key: str,
        agent: Any,
        specification_artifact: dict[str, Any],
        run_id: str,
    ) -> tuple[dict[str, Any], float]:
        """Run one reviewer inside its own isolated harness with timeout guard."""
        agent_name = getattr(agent, "name", f"{reviewer_key}-review-agent")
        harness = self._build_isolated_harness(reviewer_key, agent_name, run_id)
        ctx = ExecutionContext(
            run_id=run_id,
            stage_name=f"review_{reviewer_key}",
            agent_name=agent_name,
        )

        raw = await asyncio.wait_for(
            agent.execute(specification_artifact, harness, ctx),
            timeout=self.per_agent_timeout_seconds,
        )
        if hasattr(raw, "output_artifact"):
            output: dict[str, Any] = dict(raw.output_artifact)
            cost = float(getattr(raw, "cost_usd", harness.budget.spent_usd))
            return output, cost
        if isinstance(raw, dict):
            return raw, float(harness.budget.spent_usd)
        raise TypeError(f"Unsupported reviewer output type from {agent_name}: {type(raw)}")

    async def execute(
        self,
        specification_artifact: dict[str, Any],
        context: ExecutionContext | None = None,
    ) -> ReviewResult:
        """Execute all specialized reviewers concurrently and synthesize the review."""
        start = time.perf_counter()
        run_id = context.run_id if context is not None else "multi-review"
        spec_id = str(specification_artifact.get("id", "spec-unknown"))

        core_reviewers: list[tuple[str, Any]] = [
            ("security", self.security_agent),
            ("performance", self.performance_agent),
            ("maintainability", self.maintainability_agent),
            ("architecture", self.architecture_agent),
        ]
        for idx, custom_agent in enumerate(self.extra_reviewers, start=1):
            key = getattr(custom_agent, "review_type", f"custom_{idx}")
            core_reviewers.append((str(key), custom_agent))

        tasks = [
            self._run_single_reviewer(key, agent, specification_artifact, run_id)
            for key, agent in core_reviewers
        ]
        results = await asyncio.gather(*tasks)

        sec_out, sec_cost = results[0]
        perf_out, perf_cost = results[1]
        maint_out, maint_cost = results[2]
        arch_out, arch_cost = results[3]

        extra_outputs = [out for out, _ in results[4:]]
        total_cost = sec_cost + perf_cost + maint_cost + arch_cost + sum(
            c for _, c in results[4:]
        )
        elapsed = time.perf_counter() - start

        consolidated = await self.synthesizer.synthesize(
            security_result=sec_out,
            performance_result=perf_out,
            maintainability_result=maint_out,
            architecture_result=arch_out,
            reviewer_outputs=extra_outputs or None,
            specification_id=spec_id,
            duration_seconds=elapsed,
            total_cost_usd=total_cost,
        )

        logger.info(
            "Multi-agent review completed in %.2fs: verdict=%s gate=%s findings=%d conflicts=%d",
            elapsed,
            consolidated.verdict,
            consolidated.gate_decision,
            len(consolidated.findings),
            len(consolidated.conflicts),
        )
        return consolidated


async def execute_multi_agent_review(
    specification_artifact: dict[str, Any],
    schema_validator: SchemaValidator,
    *,
    harness_factory: Callable[[str], Harness] | None = None,
    model_adapter: ModelAdapter | None = None,
    context: ExecutionContext | None = None,
    synthesizer: ReviewSynthesizer | None = None,
    extra_reviewers: list[Any] | None = None,
) -> ReviewResult:
    """Convenience entry point to execute parallel 4-agent independent review."""
    orchestrator = MultiAgentReviewOrchestrator(
        schema_validator=schema_validator,
        harness_factory=harness_factory,
        model_adapter=model_adapter,
        synthesizer=synthesizer,
        extra_reviewers=extra_reviewers,
    )
    return await orchestrator.execute(specification_artifact, context=context)

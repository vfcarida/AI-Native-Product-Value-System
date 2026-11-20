"""
Decision Agent integration for automatic ADR draft generation with human approval (EVO-018).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from product_value_system.adr.manager import ADRManager
from product_value_system.adr.models import ADR, ADRStatus


class DecisionAgentADRIntegration:
    """
    Automatically generates proposed Architecture Decision Records whenever an
    agent makes an architectural or technology selection decision, capturing
    alternatives evaluated and experiment results that informed the choice.

    All auto-generated ADRs default to ``proposed`` status so a human reviewer
    must explicitly approve them via ``approve_adr()`` or ``pvs adr accept``.
    """

    def __init__(
        self,
        adr_manager: ADRManager,
        llm_callable: Callable[..., Any] | None = None,
    ) -> None:
        self.adr_manager = adr_manager
        self.llm = llm_callable

    async def generate_adr_from_decision(
        self,
        decision_context: dict[str, Any],
        alternatives: list[dict[str, Any]],
        chosen_alternative: str,
        experiment_results: dict[str, Any] | None = None,
        *,
        related_requirements: list[str] | None = None,
        related_code: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """
        Generate a ``proposed`` ADR draft from a structured agent decision.

        Returns:
            Canonical ADR identifier (e.g., ``ADR-0001``)
        """
        exp_results = experiment_results or {}
        prompt = self._build_prompt(
            decision_context=decision_context,
            alternatives=alternatives,
            chosen_alternative=chosen_alternative,
            experiment_results=exp_results,
        )

        llm_content = ""
        if self.llm is not None:
            try:
                raw = self.llm(prompt, temperature=0.3)
                if inspect.isawaitable(raw):
                    raw = await raw
                llm_content = str(raw or "")
            except Exception:
                llm_content = ""

        parsed_context = self.adr_manager._extract_section(llm_content, "Context")
        parsed_decision = self.adr_manager._extract_section(llm_content, "Decision")
        parsed_alts = self.adr_manager._parse_alternatives(llm_content)
        parsed_cons = self.adr_manager._parse_consequences(llm_content)
        parsed_plan = self.adr_manager._extract_section(llm_content, "Implementation Plan")
        parsed_val = self.adr_manager._parse_validation(llm_content)

        problem = decision_context.get("problem", "Architectural decision required.")
        goal = decision_context.get("goal", "Select optimal architecture option.")
        fallback_context = f"**Problem**: {problem}\n\n**Goal**: {goal}"
        fallback_decision = f"We will adopt **{chosen_alternative}**."

        if not parsed_alts:
            parsed_alts = [
                {
                    "name": str(alt["name"]),
                    "pros": str(alt.get("pros") or alt.get("description") or "Evaluated candidate"),
                    "cons": str(alt.get("cons") or "See trade-off matrix"),
                    "verdict": (
                        "Selected"
                        if str(alt["name"]).lower() == chosen_alternative.lower()
                        else str(alt.get("verdict") or "Rejected in favor of chosen alternative")
                    ),
                }
                for alt in alternatives
                if isinstance(alt, dict) and alt.get("name")
            ]

        if not any(parsed_cons.values()):
            parsed_cons = {
                "positive": [f"Adopts {chosen_alternative} aligned with stated goals"],
                "negative": ["Requires team onboarding and ongoing maintenance"],
                "neutral": ["Requires human review before transitioning from proposed to accepted"],
            }

        references = self._extract_experiment_references(exp_results)
        if not parsed_val:
            parsed_val = [
                f"Verify {chosen_alternative} meets defined non-functional requirements",
                "Obtain human architect approval before marking ADR as accepted",
            ]

        adr = self.adr_manager.create(
            title=str(decision_context.get("title", f"Adopt {chosen_alternative}")),
            author="decision-agent",
            context=parsed_context or fallback_context,
            decision=parsed_decision or fallback_decision,
            status=ADRStatus.PROPOSED.value,
            alternatives=parsed_alts,
            consequences=parsed_cons,
            implementation_plan=parsed_plan or f"Implement and validate {chosen_alternative}.",
            validation=parsed_val,
            references=references,
            related_requirements=related_requirements,
            related_code=related_code,
            tags=list(tags or ["agent-decision"]),
        )
        return adr.metadata.id

    def approve_adr(self, adr_id: str, *, reviewer: str) -> ADR:
        """Human review gate transitioning an agent-proposed ADR to ``accepted``."""
        return self.adr_manager.accept(adr_id, reviewer=reviewer)

    def _build_prompt(
        self,
        *,
        decision_context: dict[str, Any],
        alternatives: list[dict[str, Any]],
        chosen_alternative: str,
        experiment_results: dict[str, Any],
    ) -> str:
        return (
            "Generate an Architecture Decision Record based on this decision:\n\n"
            f"**Context**: {decision_context.get('problem', '')}\n"
            f"**Goal**: {decision_context.get('goal', '')}\n\n"
            "**Alternatives Considered**:\n"
            f"{self._format_alternatives(alternatives)}\n\n"
            f"**Decision**: {chosen_alternative}\n\n"
            "**Experiment Results**:\n"
            f"{self._format_experiment_results(experiment_results)}\n\n"
            "Return Markdown sections: ## Context, ## Decision, ## Alternatives Considered, "
            "## Consequences, ## Implementation Plan, ## Validation."
        )

    def _format_alternatives(self, alternatives: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for alt in alternatives:
            lines.append(f"- **{alt['name']}**: {alt.get('description', '')}")
            if "metrics" in alt:
                lines.append(f"  Metrics: {alt['metrics']}")
        return "\n".join(lines)

    def _format_experiment_results(self, results: dict[str, Any]) -> str:
        lines: list[str] = []
        for exp_id, data in results.items():
            if exp_id == "experiment_ids":
                continue
            if isinstance(data, dict):
                lines.append(f"- **{exp_id}**: winner={data.get('winner', 'N/A')}, "
                             f"confidence={data.get('confidence', 'N/A')}")
                metrics = data.get("metrics")
                if isinstance(metrics, dict):
                    for metric, value in metrics.items():
                        lines.append(f"  {metric}: {value}")
            else:
                lines.append(f"- **{exp_id}**: {data}")
        return "\n".join(lines)

    def _extract_experiment_references(self, results: dict[str, Any]) -> list[str]:
        refs: list[str] = []
        explicit_ids = results.get("experiment_ids")
        if isinstance(explicit_ids, list):
            for exp_id in explicit_ids:
                refs.append(f"Experiment: {exp_id}")

        for exp_id, data in results.items():
            if exp_id == "experiment_ids":
                continue
            if isinstance(data, dict):
                winner = data.get("winner") or data.get("recommendation", "")
                conf = data.get("confidence")
                detail = f" (winner={winner}" if winner else ""
                if conf is not None:
                    detail += f", confidence={conf}" if detail else f" (confidence={conf}"
                if detail:
                    detail += ")"
                refs.append(f"Experiment: {exp_id}{detail}")
            else:
                refs.append(f"Experiment: {exp_id} ({data})")
        return refs

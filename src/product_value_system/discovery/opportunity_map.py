"""
Opportunity Map integration bridging synthesized customer interviews with
Continuous Discovery (EVO-005 / EVO-022).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.discovery.insight_extractor import (
    SEVERITY_RANK,
    PainPointSeverity,
)
from product_value_system.discovery.theme_clusterer import ClusteredTheme


@dataclass
class Opportunity:
    """Product discovery opportunity backed by customer interview evidence."""

    id: str
    name: str
    description: str
    evidence: list[str] = field(default_factory=list)
    customer_quotes: list[str] = field(default_factory=list)
    interview_ids: list[str] = field(default_factory=list)
    frequency: int = 1
    severity: PainPointSeverity = PainPointSeverity.MEDIUM
    priority_score: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "evidence": list(self.evidence),
            "customer_quotes": list(self.customer_quotes),
            "interview_ids": list(self.interview_ids),
            "frequency": self.frequency,
            "severity": self.severity.value,
            "priority_score": round(self.priority_score, 3),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Opportunity:
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            evidence=[str(e) for e in data.get("evidence", [])],
            customer_quotes=[str(q) for q in data.get("customer_quotes", [])],
            interview_ids=[str(i) for i in data.get("interview_ids", [])],
            frequency=int(data.get("frequency", 1)),
            severity=PainPointSeverity(data.get("severity", PainPointSeverity.MEDIUM.value)),
            priority_score=float(data.get("priority_score", 0.0)),
            created_at=str(data.get("created_at", datetime.now(UTC).isoformat())),
        )


class OpportunityMap:
    """Teresa Torres-style Opportunity Solution Tree / Opportunity Map."""

    def __init__(self, desired_outcome: str = "Deliver measurable customer value") -> None:
        self.desired_outcome = desired_outcome
        self.opportunities: list[Opportunity] = []

    def add_opportunity(
        self,
        name: str,
        description: str,
        evidence: list[str] | None = None,
        customer_quotes: list[str] | None = None,
        *,
        interview_ids: list[str] | None = None,
        frequency: int | None = None,
        severity: PainPointSeverity = PainPointSeverity.MEDIUM,
    ) -> Opportunity:
        """Add (or merge by name) a customer opportunity backed by interview evidence."""
        ev_list = list(evidence or [])
        quote_list = [q for q in (customer_quotes or []) if q]
        id_list = sorted(set(interview_ids or []))
        resolved_freq = frequency if frequency is not None else max(1, len(id_list), len(ev_list))

        # Merge if an opportunity with the same normalized name already exists
        for existing in self.opportunities:
            if existing.name.strip().lower() == name.strip().lower():
                existing.description = description or existing.description
                for ev in ev_list:
                    if ev not in existing.evidence:
                        existing.evidence.append(ev)
                for q in quote_list:
                    if q not in existing.customer_quotes:
                        existing.customer_quotes.append(q)
                existing.interview_ids = sorted(set(existing.interview_ids) | set(id_list))
                existing.frequency = max(
                    existing.frequency, resolved_freq, len(existing.interview_ids)
                )
                if SEVERITY_RANK[severity] > SEVERITY_RANK[existing.severity]:
                    existing.severity = severity
                existing.priority_score = self._compute_priority(
                    existing.frequency, existing.severity
                )
                return existing

        opp_id = f"OPP-{len(self.opportunities) + 1:03d}"
        opp = Opportunity(
            id=opp_id,
            name=name.strip(),
            description=description.strip(),
            evidence=ev_list,
            customer_quotes=quote_list,
            interview_ids=id_list,
            frequency=resolved_freq,
            severity=severity,
            priority_score=self._compute_priority(resolved_freq, severity),
        )
        self.opportunities.append(opp)
        return opp

    def populate_from_themes(self, themes: list[ClusteredTheme]) -> list[Opportunity]:
        """Convert clustered interview themes into prioritized Opportunity Map entries."""
        added: list[Opportunity] = []
        for theme in themes:
            opp = self.add_opportunity(
                name=theme.theme_name,
                description=(
                    f"Mentioned in {theme.frequency} interview(s)"
                    + (
                        f" ({theme.prevalence_pct:.0f}% of {theme.total_interviews})"
                        if theme.total_interviews > 0
                        else ""
                    )
                ),
                evidence=[p.description for p in theme.pain_points],
                customer_quotes=[theme.representative_quote] if theme.representative_quote else [],
                interview_ids=theme.interview_ids,
                frequency=theme.frequency,
                severity=theme.highest_severity,
            )
            added.append(opp)
        return added

    def prioritized(self) -> list[Opportunity]:
        """Return opportunities sorted from highest priority score to lowest."""
        return sorted(
            self.opportunities,
            key=lambda o: (o.priority_score, o.frequency, SEVERITY_RANK[o.severity]),
            reverse=True,
        )

    def to_problem_context(self) -> dict[str, Any]:
        """Export structured discovery context consumable by DiscoveryAgent (EVO-005)."""
        ranked = self.prioritized()
        return {
            "desired_outcome": self.desired_outcome,
            "opportunity_count": len(ranked),
            "top_opportunities": [
                {
                    "id": o.id,
                    "name": o.name,
                    "severity": o.severity.value,
                    "frequency": o.frequency,
                    "priority_score": round(o.priority_score, 3),
                    "representative_quote": o.customer_quotes[0] if o.customer_quotes else "",
                }
                for o in ranked[:5]
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired_outcome": self.desired_outcome,
            "opportunity_count": len(self.opportunities),
            "opportunities": [o.to_dict() for o in self.prioritized()],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Opportunity Map",
            "",
            f"**Desired Outcome:** {self.desired_outcome}",
            "",
            "| ID | Opportunity | Severity | Interviews | Priority | Representative Quote |",
            "| :--- | :--- | :---: | :---: | :---: | :--- |",
        ]
        for opp in self.prioritized():
            quote = (opp.customer_quotes[0] if opp.customer_quotes else "").replace("|", "\\|")
            lines.append(
                f"| `{opp.id}` | {opp.name} | {opp.severity.value.upper()} | "
                f"{opp.frequency} | {opp.priority_score:.1f} | \"{quote}\" |"
            )
        lines.append("")
        return "\n".join(lines)

    def save(self, path: Path | str) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return out

    @classmethod
    def load(cls, path: Path | str) -> OpportunityMap:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        omap = cls(desired_outcome=str(data.get("desired_outcome", "")))
        omap.opportunities = [Opportunity.from_dict(o) for o in data.get("opportunities", [])]
        return omap

    @staticmethod
    def _compute_priority(frequency: int, severity: PainPointSeverity) -> float:
        return round(frequency * 10.0 * (SEVERITY_RANK[severity] / 2.0), 3)

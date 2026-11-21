"""
Multi-source evidence collector supporting interviews (EVO-022), support tickets (JSON/CSV),
product analytics, competitor research documents, and external URL references (EVO-023).
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.discovery.insight_extractor import InterviewInsights
from product_value_system.evidence.models import (
    Evidence,
    EvidenceQuality,
    EvidenceType,
)


def _parse_iso_datetime(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


class EvidenceCollector:
    """Collect and normalize product evidence from heterogeneous discovery sources."""

    def collect_from_interviews(
        self,
        interview_synthesis_files: Sequence[str | Path],
        *,
        include_jtbd: bool = False,
    ) -> list[Evidence]:
        """Collect primary evidence from EVO-022 interview synthesis JSON files."""
        evidence_list: list[Evidence] = []

        for raw_path in interview_synthesis_files:
            path = Path(raw_path)
            synthesis = json.loads(path.read_text(encoding="utf-8"))
            interview_id = str(synthesis.get("interview_id", path.stem))
            date = _parse_iso_datetime(synthesis.get("date") or synthesis.get("created_at"))

            for idx, pain_point in enumerate(synthesis.get("pain_points", [])):
                evidence_list.append(
                    Evidence(
                        id=f"INT-{interview_id}-PP-{idx}",
                        type=EvidenceType.INTERVIEW,
                        quality=EvidenceQuality.PRIMARY,
                        source=str(raw_path),
                        date=date,
                        claim=str(pain_point["description"]),
                        supporting_data={
                            "quotes": list(pain_point.get("quotes", [])),
                            "severity": pain_point.get("severity"),
                            "interview_id": interview_id,
                        },
                        sample_size=1,
                        methodology="Customer interview",
                        confidence=float(pain_point.get("confidence", 0.8)),
                    )
                )

            if include_jtbd:
                for idx, jtbd in enumerate(synthesis.get("jobs_to_be_done", [])):
                    evidence_list.append(
                        Evidence(
                            id=f"INT-{interview_id}-JTBD-{idx}",
                            type=EvidenceType.INTERVIEW,
                            quality=EvidenceQuality.PRIMARY,
                            source=str(raw_path),
                            date=date,
                            claim=f"Customer job: {jtbd['job']}",
                            supporting_data={
                                "situation": jtbd.get("situation", ""),
                                "motivation": jtbd.get("motivation", ""),
                                "expected_outcome": jtbd.get("expected_outcome", ""),
                                "current_solution": jtbd.get("current_solution", ""),
                                "quotes": list(jtbd.get("quotes", [])),
                            },
                            sample_size=1,
                            methodology="Customer interview (JTBD)",
                            confidence=float(jtbd.get("confidence", 0.8)),
                        )
                    )

        return evidence_list

    def collect_from_interview_insights(
        self,
        insights_list: Sequence[InterviewInsights],
        *,
        include_jtbd: bool = False,
    ) -> list[Evidence]:
        """Collect primary evidence directly from in-memory EVO-022 InterviewInsights objects."""
        evidence_list: list[Evidence] = []
        for insights in insights_list:
            date = _parse_iso_datetime(insights.created_at)
            for idx, pp in enumerate(insights.pain_points):
                evidence_list.append(
                    Evidence(
                        id=f"INT-{insights.interview_id}-PP-{idx}",
                        type=EvidenceType.INTERVIEW,
                        quality=EvidenceQuality.PRIMARY,
                        source=f"interview:{insights.interview_id}",
                        date=date,
                        claim=pp.description,
                        supporting_data={
                            "quotes": list(pp.quotes),
                            "severity": pp.severity.value,
                            "interview_id": insights.interview_id,
                        },
                        sample_size=1,
                        methodology="Customer interview",
                        confidence=pp.confidence,
                    )
                )
            if include_jtbd:
                for idx, job in enumerate(insights.jobs_to_be_done):
                    evidence_list.append(
                        Evidence(
                            id=f"INT-{insights.interview_id}-JTBD-{idx}",
                            type=EvidenceType.INTERVIEW,
                            quality=EvidenceQuality.PRIMARY,
                            source=f"interview:{insights.interview_id}",
                            date=date,
                            claim=f"Customer job: {job.job}",
                            supporting_data={
                                "situation": job.situation,
                                "motivation": job.motivation,
                                "expected_outcome": job.expected_outcome,
                                "current_solution": job.current_solution,
                            },
                            sample_size=1,
                            methodology="Customer interview (JTBD)",
                            confidence=job.confidence,
                        )
                    )
        return evidence_list

    def collect_from_support_tickets(self, ticket_file: str | Path) -> list[Evidence]:
        """Collect aggregated primary evidence from a support ticket export (JSON or CSV)."""
        path = Path(ticket_file)
        tickets: list[dict[str, Any]]
        if path.suffix.lower() == ".csv":
            with path.open(encoding="utf-8", newline="") as handle:
                tickets = list(csv.DictReader(handle))
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            tickets = raw if isinstance(raw, list) else list(raw.get("tickets", []))

        issue_groups: dict[str, dict[str, Any]] = {}
        for ticket in tickets:
            issue = str(
                ticket.get("issue_type")
                or ticket.get("category")
                or ticket.get("tag")
                or "unknown"
            ).strip()
            group = issue_groups.setdefault(
                issue,
                {"count": 0, "examples": [], "dates": [], "platforms": set()},
            )
            group["count"] += 1
            desc = str(ticket.get("description") or ticket.get("summary") or "").strip()
            if desc and len(group["examples"]) < 3:
                group["examples"].append(desc)
            if ticket.get("date") or ticket.get("created_at"):
                group["dates"].append(
                    _parse_iso_datetime(ticket.get("date") or ticket.get("created_at"))
                )
            if ticket.get("platform"):
                group["platforms"].add(str(ticket["platform"]))

        evidence_list: list[Evidence] = []
        for issue_type, data in issue_groups.items():
            most_recent = max(data["dates"]) if data["dates"] else datetime.now(UTC)
            readable_issue = issue_type.replace("_", " ")
            count = int(data["count"])
            platforms = sorted(data["platforms"])
            platform_suffix = f" (platforms: {', '.join(platforms)})" if platforms else ""
            evidence_list.append(
                Evidence(
                    id=f"TICKET-{re.sub(r'[^a-zA-Z0-9_-]+', '_', issue_type)}",
                    type=EvidenceType.SUPPORT_TICKET,
                    quality=EvidenceQuality.PRIMARY,
                    source=str(ticket_file),
                    date=most_recent,
                    claim=(
                        f"Users report {count} support complaints about {readable_issue}"
                        f"{platform_suffix}"
                    ),
                    supporting_data={
                        "issue_type": issue_type,
                        "frequency": count,
                        "examples": data["examples"],
                        "platforms": platforms,
                    },
                    sample_size=count,
                    methodology="Support ticket analysis",
                    confidence=0.9,
                )
            )
        return evidence_list

    def collect_from_analytics(
        self,
        analytics_report: dict[str, Any] | str | Path,
        *,
        source_name: str | None = None,
    ) -> list[Evidence]:
        """Collect quantitative primary evidence from an analytics dict or JSON file."""
        if isinstance(analytics_report, (str, Path)):
            path = Path(analytics_report)
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            resolved_source = source_name or str(path)
        else:
            data = analytics_report
            resolved_source = source_name or "analytics_platform"

        evidence_list: list[Evidence] = []
        for metric_name, metric_data in data.items():
            if not isinstance(metric_data, dict):
                metric_dict: dict[str, Any] = {"value": metric_data}
            else:
                metric_dict = metric_data

            claim = str(
                metric_dict.get("claim")
                or self._generate_claim_from_metric(metric_name, metric_dict)
            )
            date = _parse_iso_datetime(metric_dict.get("date"))
            sample_size = (
                int(metric_dict["sample_size"])
                if metric_dict.get("sample_size") is not None
                else None
            )
            evidence_list.append(
                Evidence(
                    id=f"ANALYTICS-{metric_name}",
                    type=EvidenceType.ANALYTICS,
                    quality=EvidenceQuality.PRIMARY,
                    source=resolved_source,
                    date=date,
                    claim=claim,
                    supporting_data=dict(metric_dict),
                    sample_size=sample_size,
                    methodology=str(metric_dict.get("methodology") or "Product analytics"),
                    confidence=float(metric_dict.get("confidence", 0.95)),
                )
            )

        return evidence_list

    def collect_from_competitor_research(
        self,
        research_doc: str | Path,
        *,
        max_claims: int = 8,
    ) -> list[Evidence]:
        """Collect secondary evidence claims from a competitor research Markdown/text document."""
        path = Path(research_doc)
        content = path.read_text(encoding="utf-8")
        claims = self._extract_claims_from_doc(content, max_claims=max_claims)
        doc_date = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)

        return [
            Evidence(
                id=f"RESEARCH-{path.stem}-{idx}",
                type=EvidenceType.COMPETITOR_RESEARCH,
                quality=EvidenceQuality.SECONDARY,
                source=str(research_doc),
                date=doc_date,
                claim=claim,
                supporting_data={"source_doc": str(research_doc)},
                sample_size=None,
                methodology="Competitor analysis",
                confidence=0.7,
            )
            for idx, claim in enumerate(claims)
        ]

    def collect_from_urls(
        self,
        urls: Sequence[str],
        *,
        content_fetcher: Callable[[str], str] | None = None,
    ) -> list[Evidence]:
        """Collect secondary evidence from external URL references or fetched content."""
        evidence_list: list[Evidence] = []
        for idx, url in enumerate(urls):
            extracted_claims: list[str] = []
            if content_fetcher is not None:
                body = content_fetcher(url)
                extracted_claims = self._extract_claims_from_doc(body, max_claims=3)
            if not extracted_claims:
                extracted_claims = [f"External reference evidence from {url}"]

            for c_idx, claim in enumerate(extracted_claims):
                evidence_list.append(
                    Evidence(
                        id=f"URL-{idx}-{c_idx}",
                        type=EvidenceType.URL_ARTICLE,
                        quality=EvidenceQuality.SECONDARY,
                        source=url,
                        date=datetime.now(UTC),
                        claim=claim,
                        supporting_data={"url": url},
                        sample_size=None,
                        methodology="External web research",
                        confidence=0.65,
                    )
                )
        return evidence_list

    @staticmethod
    def _generate_claim_from_metric(metric_name: str, metric_data: dict[str, Any]) -> str:
        value = metric_data.get("value", 0)
        if metric_name == "biometric_adoption_rate":
            return f"Biometric adoption rate is {float(value) * 100:.1f}%"
        if metric_name == "transfer_completion_rate":
            return f"Transfer completion rate is {float(value) * 100:.1f}%"
        if metric_name == "avg_transfer_time":
            return f"Average transfer time is {float(value):.1f} seconds"
        if isinstance(value, float) and 0.0 <= value <= 1.0 and "rate" in metric_name:
            readable = metric_name.replace("_", " ").capitalize()
            return f"{readable} is {value * 100:.1f}%"
        readable = metric_name.replace("_", " ").capitalize()
        unit = f" {metric_data['unit']}" if metric_data.get("unit") else ""
        return f"{readable} is {value}{unit}"

    @staticmethod
    def _extract_claims_from_doc(content: str, *, max_claims: int = 8) -> list[str]:
        claims: list[str] = []
        # Extract Markdown bullet findings first, then H2/H3 headings
        bullets = re.findall(r"^\s*[-*]\s+(.+)$", content, re.MULTILINE)
        headings = re.findall(r"^#{2,3}\s+(.+)$", content, re.MULTILINE)
        for candidate in [*headings, *bullets]:
            cleaned = candidate.strip().strip("*").strip()
            if len(cleaned) >= 8 and cleaned not in claims:
                claims.append(cleaned)
            if len(claims) >= max_claims:
                break
        if not claims and content.strip():
            first_line = content.strip().splitlines()[0].strip()
            claims.append(first_line[:160])
        return claims

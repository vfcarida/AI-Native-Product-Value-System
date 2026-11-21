"""
High-level EvidenceSynthesisAgent orchestrating collection, quality scoring,
contradiction detection, triangulation, and report/network generation (EVO-023).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from product_value_system.discovery.insight_extractor import InterviewInsights
from product_value_system.evidence.collector import EvidenceCollector
from product_value_system.evidence.contradiction_detector import (
    ContradictionDetector,
    EmbeddingCallable,
    LLMCallable,
)
from product_value_system.evidence.models import Evidence, SynthesisReport
from product_value_system.evidence.quality_scorer import QualityScorer
from product_value_system.evidence.reporter import (
    render_evidence_network_html,
    render_synthesis_json,
    render_synthesis_markdown,
)
from product_value_system.evidence.synthesis_engine import SynthesisEngine


class EvidenceSynthesisAgent:
    """Agent facade synthesizing multi-source product discovery evidence."""

    name = "evidence-synthesis-agent"
    version = "0.2.0"

    def __init__(
        self,
        *,
        llm_callable: LLMCallable | None = None,
        embedding_model: EmbeddingCallable | None = None,
        quality_scorer: QualityScorer | None = None,
        collector: EvidenceCollector | None = None,
    ) -> None:
        self.collector = collector or EvidenceCollector()
        self.quality_scorer = quality_scorer or QualityScorer()
        self.contradiction_detector = ContradictionDetector(
            llm_callable=llm_callable,
            embedding_model=embedding_model,
        )
        self.engine = SynthesisEngine(
            contradiction_detector=self.contradiction_detector,
            quality_scorer=self.quality_scorer,
            llm_callable=llm_callable,
        )

    async def synthesize_sources(
        self,
        topic: str,
        *,
        interview_files: Sequence[str | Path] = (),
        interview_insights: Sequence[InterviewInsights] = (),
        ticket_file: str | Path | None = None,
        analytics: dict[str, Any] | str | Path | None = None,
        research_docs: Sequence[str | Path] = (),
        urls: Sequence[str] = (),
        extra_evidence: Sequence[Evidence] = (),
        similarity_threshold: float = 0.7,
        grouping_threshold: float = 0.55,
    ) -> SynthesisReport:
        """Collect evidence across all configured sources and synthesize a report."""
        evidence: list[Evidence] = list(extra_evidence)

        if interview_files:
            evidence.extend(self.collector.collect_from_interviews(interview_files))
        if interview_insights:
            evidence.extend(self.collector.collect_from_interview_insights(interview_insights))
        if ticket_file is not None:
            evidence.extend(self.collector.collect_from_support_tickets(ticket_file))
        if analytics is not None:
            evidence.extend(self.collector.collect_from_analytics(analytics))
        for doc in research_docs:
            evidence.extend(self.collector.collect_from_competitor_research(doc))
        if urls:
            evidence.extend(self.collector.collect_from_urls(urls))

        return await self.engine.synthesize(
            topic=topic,
            evidence_list=evidence,
            similarity_threshold=similarity_threshold,
            grouping_threshold=grouping_threshold,
        )

    @staticmethod
    def save_report(
        report: SynthesisReport,
        output_path: Path | str,
        *,
        fmt: str | None = None,
    ) -> Path:
        """Save SynthesisReport to JSON or Markdown (inferred from extension if fmt omitted)."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        resolved_fmt = (fmt or ("json" if out.suffix.lower() == ".json" else "markdown")).lower()
        content = (
            render_synthesis_json(report)
            if resolved_fmt == "json"
            else render_synthesis_markdown(report)
        )
        out.write_text(content, encoding="utf-8")
        return out

    @staticmethod
    def save_network_html(report: SynthesisReport, output_path: Path | str) -> Path:
        """Save interactive HTML/SVG evidence network visualization."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_evidence_network_html(report), encoding="utf-8")
        return out

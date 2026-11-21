"""
End-to-end Customer Interview Synthesis facade (EVO-022).

Re-exports ``TranscriptSegment``, ``ProcessedTranscript``, and ``TranscriptProcessor``
and provides ``InterviewSynthesizer`` for single-file, batch, clustering, and
Opportunity Map workflows.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from product_value_system.discovery.exporters import export_insights
from product_value_system.discovery.insight_extractor import (
    InsightExtractor,
    InterviewInsights,
    LLMCallable,
)
from product_value_system.discovery.opportunity_map import OpportunityMap
from product_value_system.discovery.pii_redactor import PIIRedactor
from product_value_system.discovery.theme_clusterer import (
    ClusteredTheme,
    EmbeddingCallable,
    ThemeClusterer,
)
from product_value_system.discovery.transcript_processor import (
    ProcessedTranscript,
    TranscriptProcessor,
    TranscriptSegment,
    format_timestamp,
)

SUPPORTED_TRANSCRIPT_EXTENSIONS = {".vtt", ".srt", ".txt", ".md"}


class InterviewSynthesizer:
    """High-level orchestrator for interview parsing, redaction, extraction, and clustering."""

    def __init__(
        self,
        llm: LLMCallable | None = None,
        embedding_model: EmbeddingCallable | None = None,
        pii_redactor: PIIRedactor | None = None,
    ) -> None:
        self.processor = TranscriptProcessor()
        self.pii_redactor = pii_redactor or PIIRedactor()
        self.extractor = InsightExtractor(llm_callable=llm, pii_redactor=self.pii_redactor)
        self.clusterer = ThemeClusterer(embedding_model=embedding_model, llm_callable=llm)

    async def synthesize(
        self,
        source: Path | str,
        *,
        interview_id: str | None = None,
        interview_date: datetime | None = None,
    ) -> InterviewInsights:
        """Synthesize insights from a transcript file path or raw transcript string."""
        if isinstance(source, Path) or (
            isinstance(source, str) and "\n" not in source and Path(source).is_file()
        ):
            transcript = self.processor.process_file(
                Path(source),
                interview_id=interview_id,
                interview_date=interview_date,
            )
        else:
            transcript = self.processor.process_transcript(
                transcript_text=str(source),
                interview_id=interview_id or "INT-001",
                interview_date=interview_date,
            )

        return await self.extractor.extract_insights(transcript)

    async def synthesize_batch(
        self,
        directory: Path | str,
        *,
        output_dir: Path | str | None = None,
        export_format: str = "json",
    ) -> list[InterviewInsights]:
        """Synthesize all transcript files in ``directory`` and optionally save outputs."""
        dir_path = Path(directory)
        files = sorted(
            p
            for p in dir_path.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_TRANSCRIPT_EXTENSIONS
        )

        results: list[InterviewInsights] = []
        ext_map = {"json": ".json", "markdown": ".md", "md": ".md", "notion": ".notion.md"}
        suffix = ext_map.get(export_format.lower(), ".json")

        out_path = Path(output_dir) if output_dir is not None else None
        if out_path is not None:
            out_path.mkdir(parents=True, exist_ok=True)

        for file_path in files:
            insights = await self.synthesize(file_path)
            results.append(insights)
            if out_path is not None:
                rendered = export_insights(insights, fmt=export_format)
                target = out_path / f"{insights.interview_id}-synthesis{suffix}"
                target.write_text(rendered, encoding="utf-8")

        return results

    async def cluster_themes(
        self,
        insights_list: list[InterviewInsights],
        *,
        similarity_threshold: float = 0.7,
        min_samples: int = 2,
        include_unclustered: bool = False,
    ) -> list[ClusteredTheme]:
        """Cluster semantically similar pain points across a collection of interviews."""
        all_pain_points = [pp for item in insights_list for pp in item.pain_points]
        return await self.clusterer.cluster_pain_points(
            pain_points=all_pain_points,
            similarity_threshold=similarity_threshold,
            min_samples=min_samples,
            total_interviews=len(insights_list),
            include_unclustered=include_unclustered,
        )

    async def populate_opportunity_map(
        self,
        insights_list: list[InterviewInsights],
        *,
        opportunity_map: OpportunityMap | None = None,
        similarity_threshold: float = 0.7,
        min_samples: int = 2,
    ) -> OpportunityMap:
        """Cluster insights and populate an OpportunityMap for EVO-005 discovery."""
        omap = opportunity_map or OpportunityMap()
        themes = await self.cluster_themes(
            insights_list,
            similarity_threshold=similarity_threshold,
            min_samples=min_samples,
            include_unclustered=True,
        )
        omap.populate_from_themes(themes)
        return omap


__all__ = [
    "InterviewSynthesizer",
    "ProcessedTranscript",
    "SUPPORTED_TRANSCRIPT_EXTENSIONS",
    "TranscriptProcessor",
    "TranscriptSegment",
    "format_timestamp",
]

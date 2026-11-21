"""
Customer Interview Synthesis & Continuous Discovery module (EVO-022).

Provides:
- Transcript parsing (VTT, SRT, plain text)
- Multi-layer PII redaction (emails, phones, CPFs, cards, addresses, names)
- Pain point, JTBD, quote, theme & summary extraction with human review workflow
- Cross-interview semantic theme clustering (DBSCAN + cosine similarity)
- Opportunity Map integration (EVO-005)
- JSON, Markdown, Notion exporters & synthesis trend dashboard
"""

from product_value_system.discovery.exporters import (
    DashboardSnapshot,
    SynthesisDashboard,
    export_clustered_themes,
    export_insights,
    export_insights_json,
    export_insights_markdown,
    export_insights_notion,
)
from product_value_system.discovery.insight_extractor import (
    InsightExtractor,
    InterviewInsights,
    JobToBeDone,
    PainPoint,
    PainPointSeverity,
    Quote,
    ReviewStatus,
)
from product_value_system.discovery.interview_synthesis import (
    InterviewSynthesizer,
    ProcessedTranscript,
    TranscriptProcessor,
    TranscriptSegment,
    format_timestamp,
)
from product_value_system.discovery.opportunity_map import (
    Opportunity,
    OpportunityMap,
)
from product_value_system.discovery.pii_redactor import (
    DEFAULT_COMMON_NAMES,
    PIIRedactor,
)
from product_value_system.discovery.theme_clusterer import (
    ClusteredTheme,
    ThemeClusterer,
    default_semantic_embeddings,
)

__all__ = [
    "ClusteredTheme",
    "DEFAULT_COMMON_NAMES",
    "DashboardSnapshot",
    "InsightExtractor",
    "InterviewInsights",
    "InterviewSynthesizer",
    "JobToBeDone",
    "Opportunity",
    "OpportunityMap",
    "PIIRedactor",
    "PainPoint",
    "PainPointSeverity",
    "ProcessedTranscript",
    "Quote",
    "ReviewStatus",
    "SynthesisDashboard",
    "ThemeClusterer",
    "TranscriptProcessor",
    "TranscriptSegment",
    "default_semantic_embeddings",
    "export_clustered_themes",
    "export_insights",
    "export_insights_json",
    "export_insights_markdown",
    "export_insights_notion",
    "format_timestamp",
]

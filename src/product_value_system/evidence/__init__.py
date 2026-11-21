"""
Multi-Source Evidence Synthesis module (EVO-023).

Collects evidence across interviews, support tickets, analytics, competitor research,
and URLs; scores source quality; detects contradictions; synthesizes weighted claims
with provenance; and renders reports & evidence network visualizations.
"""

from product_value_system.evidence.agent import EvidenceSynthesisAgent
from product_value_system.evidence.collector import EvidenceCollector
from product_value_system.evidence.contradiction_detector import ContradictionDetector
from product_value_system.evidence.models import (
    Claim,
    Contradiction,
    Evidence,
    EvidenceQuality,
    EvidenceType,
    SynthesisReport,
)
from product_value_system.evidence.quality_scorer import QualityScorer
from product_value_system.evidence.reporter import (
    render_evidence_network_html,
    render_evidence_network_mermaid,
    render_synthesis_json,
    render_synthesis_markdown,
)
from product_value_system.evidence.synthesis_engine import SynthesisEngine

__all__ = [
    "Claim",
    "Contradiction",
    "ContradictionDetector",
    "Evidence",
    "EvidenceCollector",
    "EvidenceQuality",
    "EvidenceSynthesisAgent",
    "EvidenceType",
    "QualityScorer",
    "SynthesisEngine",
    "SynthesisReport",
    "render_evidence_network_html",
    "render_evidence_network_mermaid",
    "render_synthesis_json",
    "render_synthesis_markdown",
]

"""
Brownfield codebase temporal analysis package (EVO-030):
git history extraction, hotspot detection, temporal coupling, code age, and ownership.
"""

from product_value_system.brownfield.age_analyzer import AgeAnalyzer, FileAge
from product_value_system.brownfield.coupling_analyzer import Coupling, CouplingAnalyzer
from product_value_system.brownfield.git_analyzer import (
    GitAnalyzer,
    GitInsights,
    load_brownfield_config,
)
from product_value_system.brownfield.hotspot_detector import Hotspot, HotspotDetector
from product_value_system.brownfield.models import CommitChange, anonymize_author
from product_value_system.brownfield.ownership_analyzer import Ownership, OwnershipAnalyzer

__all__ = [
    "AgeAnalyzer",
    "CommitChange",
    "Coupling",
    "CouplingAnalyzer",
    "FileAge",
    "GitAnalyzer",
    "GitInsights",
    "Hotspot",
    "HotspotDetector",
    "Ownership",
    "OwnershipAnalyzer",
    "anonymize_author",
    "load_brownfield_config",
]

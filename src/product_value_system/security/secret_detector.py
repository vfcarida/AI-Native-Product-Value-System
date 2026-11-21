"""
Unified secret detection facade switching to HybridSecretDetector (EVO-024).
"""

from __future__ import annotations

from pathlib import Path

from product_value_system.security.hybrid_detector import (
    HybridSecretDetector,
    SecretFinding,
    load_allowlist,
)
from product_value_system.security.ml_secret_detector import (
    MLAnalysisResult,
    MLSecretDetector,
)
from product_value_system.security.secret_detection import SecretDetector, redact_dict

_DETECTOR_CACHE: dict[tuple[bool, float, str | None], HybridSecretDetector] = {}


def detect_secrets(
    text: str,
    use_ml: bool = True,
    threshold: float = 0.7,
    allowlist_path: Path | str | None = None,
) -> list[SecretFinding]:
    """
    Detect secrets in ``text`` using the hybrid regex + ML detector.

    Args:
        text: Input text to scan.
        use_ml: Enable ML contextual validation and entropy/base64 scanning.
        threshold: Minimum combined confidence score (0.0–1.0).
        allowlist_path: Optional YAML allowlist file path.

    Returns:
        Deduplicated list of ``SecretFinding`` instances.
    """
    key = (use_ml, round(float(threshold), 4), str(allowlist_path) if allowlist_path else None)
    detector = _DETECTOR_CACHE.get(key)
    if detector is None:
        detector = HybridSecretDetector(
            use_ml=use_ml,
            confidence_threshold=threshold,
            allowlist_path=allowlist_path,
        )
        _DETECTOR_CACHE[key] = detector
    return detector.detect(text)


def reset_detector_cache() -> None:
    """Clear cached detector instances (primarily for test isolation)."""
    _DETECTOR_CACHE.clear()


__all__ = [
    "HybridSecretDetector",
    "MLAnalysisResult",
    "MLSecretDetector",
    "SecretDetector",
    "SecretFinding",
    "detect_secrets",
    "load_allowlist",
    "redact_dict",
    "reset_detector_cache",
]

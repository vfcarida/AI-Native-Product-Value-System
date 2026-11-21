"""
Hybrid secret detector combining a sub-10ms regex fast path with lazy-loaded
ML contextual validation, Shannon entropy + Base64 detection, caching, and allowlists (EVO-024).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from product_value_system.security.ml_secret_detector import (
    MLAnalysisResult,
    MLSecretDetector,
    calculate_shannon_entropy,
    inspect_base64_secret,
)


@dataclass
class SecretFinding:
    """Structured secret detection finding with span offsets, confidence, and method."""

    type: str
    start: int
    end: int
    confidence: float
    snippet: str
    detection_method: str = "hybrid"  # regex, ml, hybrid, entropy

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "start": self.start,
            "end": self.end,
            "confidence": round(self.confidence, 4),
            "snippet_preview": (
                self.snippet[:12] + "..." if len(self.snippet) > 12 else self.snippet
            ),
            "detection_method": self.detection_method,
        }


def load_allowlist(path: Path | str) -> list[str]:
    """Load allowlist patterns from a YAML configuration file."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        return []
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    entries = data.get("allowlist", [])
    patterns: list[str] = []
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            patterns.append(entry.strip())
        elif isinstance(entry, dict) and entry.get("pattern"):
            patterns.append(str(entry["pattern"]).strip())
    return patterns


class HybridSecretDetector:
    """Three-stage secret detector: Regex fast path -> ML validation -> Entropy/Base64 scan."""

    _TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{20,}(?![A-Za-z0-9+/=_-])")
    _FOUR_DIGITS_RE = re.compile(r"\d{4}")
    _CASE_SENSITIVE_ENTITIES: frozenset[str] = frozenset(
        {
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GITHUB_TOKEN",
            "AWS_ACCESS_KEY",
            "SLACK_TOKEN",
            "PRIVATE_KEY",
            "CREDIT_CARD",
        }
    )

    def __init__(
        self,
        use_ml: bool = True,
        confidence_threshold: float = 0.7,
        regex_patterns: dict[str, str] | None = None,
        *,
        entity_types: Sequence[str] | None = None,
        allowlist: Sequence[str] | None = None,
        allowlist_path: Path | str | None = None,
        entropy_threshold: float = 4.2,
        ml_detector: MLSecretDetector | None = None,
    ) -> None:
        self.use_ml = use_ml
        self.threshold = float(confidence_threshold)
        self.entropy_threshold = float(entropy_threshold)
        self.entity_types: set[str] | None = set(entity_types) if entity_types else None

        raw_patterns = regex_patterns or self._default_patterns()
        if self.entity_types is not None:
            raw_patterns = {
                k: v for k, v in raw_patterns.items() if k in self.entity_types or "API_KEY" in k
            }
        self.regex_patterns: dict[str, str] = raw_patterns
        self._compiled_patterns: list[tuple[str, re.Pattern[str]]] = [
            (
                entity,
                re.compile(
                    pattern,
                    0 if entity in self._CASE_SENSITIVE_ENTITIES else re.IGNORECASE,
                ),
            )
            for entity, pattern in self.regex_patterns.items()
        ]

        allow_items: list[str] = list(allowlist or [])
        if allowlist_path is not None:
            allow_items.extend(load_allowlist(allowlist_path))
        self.allowlist: list[str] = allow_items
        self._compiled_allowlist: list[re.Pattern[str]] = [
            re.compile(re.escape(item), re.IGNORECASE) for item in self.allowlist if item
        ]

        # Lazy ML initialization for zero overhead when use_ml=False
        self._ml_detector: MLSecretDetector | None = ml_detector
        self._analyze_cached = lru_cache(maxsize=2048)(self._analyze_uncached)

    @property
    def ml_detector(self) -> MLSecretDetector | None:
        if self.use_ml and self._ml_detector is None:
            self._ml_detector = MLSecretDetector(confidence_threshold=self.threshold)
        return self._ml_detector

    @classmethod
    def from_config(cls, config_path: Path | str) -> HybridSecretDetector:
        """Instantiate HybridSecretDetector from a YAML configuration file."""
        cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        sec = cfg.get("secret_detection", cfg)
        allowlist_file = sec.get("allowlist_file")
        return cls(
            use_ml=bool(sec.get("use_ml", True)),
            confidence_threshold=float(sec.get("confidence_threshold", 0.7)),
            entity_types=sec.get("entity_types"),
            allowlist=sec.get("allowlist"),
            allowlist_path=allowlist_file,
            entropy_threshold=float(sec.get("entropy_threshold", 4.2)),
        )

    def detect(self, text: str) -> list[SecretFinding]:
        """Run hybrid secret detection and return deduplicated, allowlist-filtered findings."""
        if not text:
            return []

        findings: list[SecretFinding] = []

        # Phase 1: Regex fast path
        regex_findings = self._detect_regex(text)

        # Phase 2: ML contextual validation of regex matches
        if self.use_ml and regex_findings:
            findings.extend(self._validate_with_ml(text, regex_findings))
        else:
            findings.extend(regex_findings)

        # Phase 3: High-entropy & base64-encoded novel secret scan
        if self.use_ml:
            already_covered = [(f.start, f.end) for f in regex_findings]
            entropy_candidates = self._detect_high_entropy(text, skip_spans=already_covered)
            if entropy_candidates:
                findings.extend(self._validate_with_ml(text, entropy_candidates))

        filtered = [f for f in findings if not self._is_allowlisted(text, f)]
        return self._deduplicate(filtered)

    def redact(self, text: str, placeholder: str = "[REDACTED]") -> str:
        """Detect and redact all findings from ``text``."""
        findings = self.detect(text)
        if not findings:
            return text
        redacted = text
        for finding in sorted(findings, key=lambda f: f.start, reverse=True):
            redacted = (
                redacted[: finding.start]
                + f"{placeholder}:{finding.type}"
                + redacted[finding.end :]
            )
        return redacted

    _PATTERN_PREFILTERS: dict[str, tuple[str, ...]] = {
        "ANTHROPIC_API_KEY": ("sk-ant-",),
        "OPENAI_API_KEY": ("sk-",),
        "GITHUB_TOKEN": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_"),
        "AWS_ACCESS_KEY": ("akia",),
        "AWS_SECRET_ACCESS_KEY": ("aws_secret_access_key",),
        "SLACK_TOKEN": ("xox",),
        "PRIVATE_KEY": ("private key",),
        "GENERIC_API_KEY": ("api",),
        "PASSWORD": ("pass",),
        "BEARER_TOKEN": ("bearer",),
    }

    def _detect_regex(self, text: str) -> list[SecretFinding]:
        """Fast regex scan catching well-known provider keys, tokens, passwords, and cards."""
        findings: list[SecretFinding] = []
        text_lower = text.lower()
        has_digits: bool | None = None

        for entity_type, compiled in self._compiled_patterns:
            prefilters = self._PATTERN_PREFILTERS.get(entity_type)
            if prefilters is not None and not any(tok in text_lower for tok in prefilters):
                continue
            if entity_type == "CREDIT_CARD":
                if has_digits is None:
                    has_digits = self._FOUR_DIGITS_RE.search(text) is not None
                if not has_digits:
                    continue
            for match in compiled.finditer(text):
                findings.append(
                    SecretFinding(
                        type=entity_type,
                        start=match.start(),
                        end=match.end(),
                        confidence=0.82,
                        snippet=match.group(0),
                        detection_method="regex",
                    )
                )
        return findings

    def _detect_high_entropy(
        self,
        text: str,
        *,
        skip_spans: list[tuple[int, int]] | None = None,
    ) -> list[SecretFinding]:
        """Identify high-entropy or base64-encoded novel secret candidates."""
        findings: list[SecretFinding] = []
        skipped = skip_spans or []

        for match in self._TOKEN_RE.finditer(text):
            start, end = match.start(), match.end()
            if any(not (end <= s or e <= start) for s, e in skipped):
                continue

            snippet = match.group(0)
            entropy = self._calculate_entropy(snippet)
            is_b64, _ = inspect_base64_secret(snippet)
            is_hex_64 = len(snippet) >= 32 and bool(re.fullmatch(r"[0-9a-fA-F]+", snippet))

            if entropy > self.entropy_threshold or is_b64 or (is_hex_64 and entropy >= 3.3):
                findings.append(
                    SecretFinding(
                        type="ENCODED_SECRET" if is_b64 else "UNKNOWN_SECRET",
                        start=start,
                        end=end,
                        confidence=0.65 if is_b64 else 0.6,
                        snippet=snippet,
                        detection_method="entropy",
                    )
                )
        return findings

    def _analyze_uncached(
        self,
        snippet: str,
        context_key: str,
        entity_type: str,
        context: str,
    ) -> MLAnalysisResult:
        assert self.ml_detector is not None
        return self.ml_detector.analyze_context(
            snippet=snippet,
            context=context,
            entity_type=entity_type,
        )

    def _validate_with_ml(
        self,
        text: str,
        candidates: list[SecretFinding],
    ) -> list[SecretFinding]:
        """Validate candidate findings with ML context scoring and combine confidences."""
        detector = self.ml_detector
        if detector is None:
            return candidates

        validated: list[SecretFinding] = []
        for candidate in candidates:
            ctx_start = max(0, candidate.start - 50)
            ctx_end = min(len(text), candidate.end + 50)
            context = text[ctx_start:ctx_end]
            context_hash = hashlib.md5(context.encode("utf-8", errors="ignore")).hexdigest()

            ml_result = self._analyze_cached(
                candidate.snippet,
                context_hash,
                candidate.type,
                context,
            )
            combined = candidate.confidence * 0.4 + ml_result.confidence * 0.6
            if combined >= self.threshold:
                validated.append(
                    SecretFinding(
                        type=ml_result.entity_type or candidate.type,
                        start=candidate.start,
                        end=candidate.end,
                        confidence=round(min(1.0, combined), 4),
                        snippet=candidate.snippet,
                        detection_method="hybrid",
                    )
                )
        return validated

    def _is_allowlisted(self, text: str, finding: SecretFinding) -> bool:
        if not self._compiled_allowlist:
            return False
        ctx_start = max(0, finding.start - 30)
        ctx_end = min(len(text), finding.end + 30)
        window = text[ctx_start:ctx_end]
        return any(
            pat.search(finding.snippet) or pat.search(window)
            for pat in self._compiled_allowlist
        )

    @staticmethod
    def _calculate_entropy(string: str) -> float:
        return calculate_shannon_entropy(string)

    def _deduplicate(self, findings: list[SecretFinding]) -> list[SecretFinding]:
        """Remove overlapping findings, preserving the highest-confidence span."""
        if not findings:
            return []
        ordered = sorted(findings, key=lambda f: (-f.confidence, f.start, -(f.end - f.start)))
        kept: list[SecretFinding] = []
        for finding in ordered:
            if not any(self._overlaps(finding, existing) for existing in kept):
                kept.append(finding)
        kept.sort(key=lambda f: f.start)
        return kept

    @staticmethod
    def _overlaps(f1: SecretFinding, f2: SecretFinding) -> bool:
        return not (f1.end <= f2.start or f2.end <= f1.start)

    @staticmethod
    def _default_patterns() -> dict[str, str]:
        """Default regex patterns for high-priority secret categories."""
        return {
            "ANTHROPIC_API_KEY": r"sk-ant-[a-zA-Z0-9_-]{20,}",
            "OPENAI_API_KEY": r"sk-[a-zA-Z0-9]{48}",
            "GITHUB_TOKEN": r"gh[pousr]_[a-zA-Z0-9]{36}",
            "AWS_ACCESS_KEY": r"AKIA[0-9A-Z]{16}",
            "AWS_SECRET_ACCESS_KEY": (
                r"aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"
            ),
            "SLACK_TOKEN": r"xox[baprs]-[a-zA-Z0-9-]{10,}",
            "PRIVATE_KEY": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "GENERIC_API_KEY": r"api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9_-]{20,}['\"]?",
            "PASSWORD": r"pass(?:word|wd)\s*[:=]\s*['\"]?[^\s'\",;]{8,}['\"]?",
            "BEARER_TOKEN": r"bearer\s+[a-zA-Z0-9\-._~+/]{16,}=*",
            "CREDIT_CARD": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        }

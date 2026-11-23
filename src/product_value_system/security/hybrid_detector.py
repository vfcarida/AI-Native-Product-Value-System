"""
Hybrid secret detector combining a sub-10ms regex fast path with Microsoft Presidio
ML contextual validation + custom AI/cloud/obfuscation recognizers, Shannon entropy +
Base64 scanning, ReDoS-safe allowlists, OTel telemetry, and audit logging (EVO-024 / PROD-001).
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import re
import time
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from product_value_system.security.ml_secret_detector import (
    SUPPORTED_SECRET_ENTITIES,
    MLAnalysisResult,
    MLSecretDetector,
    PresidioSpanMatch,
    calculate_shannon_entropy,
    inspect_base64_secret,
)

logger = logging.getLogger(__name__)

_PRESIDIO_AVAILABLE: bool = (
    importlib.util.find_spec("presidio_analyzer") is not None
    and importlib.util.find_spec("presidio_anonymizer") is not None
)
_PRESIDIO_MISSING_WARNED: bool = False

try:
    from opentelemetry import trace as _otel_trace

    _TRACER: Any | None = _otel_trace.get_tracer(__name__)
except Exception:
    _TRACER = None

# Heuristic guard rejecting catastrophic nested quantifiers in user-supplied regex allowlists
_REDOS_NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)[+*{]")
_MAX_ALLOWLIST_PATTERN_LEN = 256


def validate_allowlist_pattern(pattern: str, *, is_regex: bool = False) -> str:
    """Validate and sanitize a user-supplied allowlist entry against ReDoS attacks."""
    cleaned = pattern.strip()
    if not cleaned:
        raise ValueError("Allowlist pattern cannot be empty")
    if len(cleaned) > _MAX_ALLOWLIST_PATTERN_LEN:
        raise ValueError(
            f"Allowlist pattern exceeds maximum length ({_MAX_ALLOWLIST_PATTERN_LEN} chars)"
        )
    if is_regex:
        if _REDOS_NESTED_QUANTIFIER_RE.search(cleaned):
            raise ValueError("Unsafe nested quantifier detected in allowlist regex pattern")
        # Ensure pattern compiles cleanly
        re.compile(cleaned, re.IGNORECASE)
    return cleaned


@dataclass
class SecretFinding:
    """Structured secret detection finding with span offsets, confidence, and source/method."""

    type: str
    start: int
    end: int
    confidence: float
    snippet: str
    detection_method: str = "hybrid"  # regex, ml, hybrid, entropy

    @property
    def value(self) -> str:
        """Alias for ``snippet`` matching the PROD-001 finding contract."""
        return self.snippet

    @property
    def source(self) -> str:
        """Alias for ``detection_method`` (``regex``, ``ml``, ``hybrid``, or ``entropy``)."""
        return self.detection_method

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
            "source": self.detection_method,
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
    """Three-stage secret detector: Regex fast path -> Presidio ML fusion -> Entropy/Base64 scan."""

    _TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{20,}(?![A-Za-z0-9+/=_-])")
    _FOUR_DIGITS_RE = re.compile(r"\d{4}")
    _CASE_SENSITIVE_ENTITIES: frozenset[str] = frozenset(
        {
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "GITHUB_TOKEN",
            "AWS_ACCESS_KEY",
            "GCP_SERVICE_ACCOUNT",
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
        require_presidio: bool = False,
        spacy_model: str = "en_core_web_sm",
    ) -> None:
        global _PRESIDIO_MISSING_WARNED

        self.require_presidio = bool(require_presidio)
        self.spacy_model = spacy_model
        self.presidio_enabled: bool = False

        if use_ml and ml_detector is None and not _PRESIDIO_AVAILABLE:
            if not _PRESIDIO_MISSING_WARNED:
                logger.warning(
                    "Presidio not installed; ML secret detection falling back. "
                    "Install with: pip install product-value-system[security-ml]"
                )
                _PRESIDIO_MISSING_WARNED = True
            if self.require_presidio:
                logger.warning(
                    "Presidio required but unavailable; forcing regex-only secret detection."
                )
                self.use_ml = False
            else:
                # Degrade gracefully to built-in ML feature classifier so CI without
                # optional spaCy/Presidio retains contextual FP filtering and entropy scans.
                self.use_ml = True
        else:
            self.use_ml = bool(use_ml)
            self.presidio_enabled = bool(
                self.use_ml and (_PRESIDIO_AVAILABLE or (ml_detector is not None))
            )

        self.threshold = float(confidence_threshold)
        self.confidence_threshold = self.threshold
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
        self._compiled_allowlist: list[re.Pattern[str]] = self._compile_allowlist(self.allowlist)

        # Lazy ML initialization for zero overhead when use_ml=False
        self._ml_detector: MLSecretDetector | None = ml_detector
        self._analyze_cached = lru_cache(maxsize=2048)(self._analyze_uncached)

    @staticmethod
    def _compile_allowlist(items: Sequence[str]) -> list[re.Pattern[str]]:
        compiled: list[re.Pattern[str]] = []
        for item in items:
            if not item or not item.strip():
                continue
            raw = item.strip()
            if raw.startswith("regex:"):
                expr = validate_allowlist_pattern(raw[6:], is_regex=True)
                compiled.append(re.compile(expr, re.IGNORECASE))
            else:
                safe_literal = validate_allowlist_pattern(raw, is_regex=False)
                compiled.append(re.compile(re.escape(safe_literal), re.IGNORECASE))
        return compiled

    @property
    def ml_detector(self) -> MLSecretDetector | None:
        if self.use_ml and self._ml_detector is None:
            self._ml_detector = MLSecretDetector(
                spacy_model=self.spacy_model,
                confidence_threshold=self.threshold,
                enable_presidio=_PRESIDIO_AVAILABLE,
            )
            self.presidio_enabled = self._ml_detector.backend == "presidio"
        return self._ml_detector

    @property
    def ml_backend(self) -> str:
        """Return active detection backend: ``presidio``, ``ml_classifier``, or ``regex``."""
        if not self.use_ml:
            return "regex"
        detector = self.ml_detector
        return detector.backend if detector is not None else "regex"

    def _get_analyzer(self) -> Any | None:
        """Return the active Presidio AnalyzerEngine (lazy-loaded), or None if unavailable."""
        detector = self.ml_detector
        return detector.analyzer if detector is not None else None

    def _get_anonymizer(self) -> Any | None:
        """Return the active Presidio AnonymizerEngine (lazy-loaded), or None if unavailable."""
        detector = self.ml_detector
        return detector.anonymizer if detector is not None else None

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

    @classmethod
    def from_env(
        cls,
        *,
        allowlist: Sequence[str] | None = None,
        allowlist_path: Path | str | None = None,
        require_presidio: bool = False,
    ) -> HybridSecretDetector:
        """Instantiate HybridSecretDetector using ``SECRET_DETECTION_*`` environment variables."""
        raw_use_ml = os.getenv("SECRET_DETECTION_USE_ML", "false").strip().lower()
        use_ml = raw_use_ml in {"1", "true", "yes", "on"}
        raw_conf = os.getenv("SECRET_DETECTION_CONFIDENCE", "0.7").strip()
        try:
            confidence = float(raw_conf)
        except ValueError:
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))
        return cls(
            use_ml=use_ml,
            confidence_threshold=confidence,
            allowlist=allowlist,
            allowlist_path=allowlist_path,
            require_presidio=require_presidio,
        )

    def find_secrets(self, text: str) -> list[SecretFinding]:
        """Run hybrid secret detection and return deduplicated, allowlist-filtered findings."""
        if not text:
            return []

        started = time.perf_counter()
        span_cm = (
            _TRACER.start_as_current_span("secret_detection.scan")
            if _TRACER is not None
            else nullcontext()
        )
        with span_cm as span:
            findings = self._run_pipeline(text)
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            backend = (
                self._ml_detector.backend
                if (self.use_ml and self._ml_detector is not None)
                else ("ml_pending" if self.use_ml else "regex")
            )
            if span is not None:
                span.set_attribute("secret_detection.use_ml", self.use_ml)
                span.set_attribute("secret_detection.backend", backend)
                span.set_attribute("secret_detection.text_length", len(text))
                span.set_attribute("secret_detection.findings_count", len(findings))
                span.set_attribute("secret_detection.latency_ms", round(elapsed_ms, 3))

            if findings:
                # Structured audit trail: NEVER logs raw secret snippets or input text
                logger.info(
                    "secret_detection.audit count=%d backend=%s entity_types=%s "
                    "sources=%s latency_ms=%.2f",
                    len(findings),
                    backend,
                    sorted({f.type for f in findings}),
                    sorted({f.source for f in findings}),
                    elapsed_ms,
                )

            return findings

    def detect(self, text: str) -> list[SecretFinding]:
        """Alias for ``find_secrets()`` preserving backward compatibility."""
        return self.find_secrets(text)

    def contains_secrets(self, text: str) -> bool:
        """Return True if any secret finding meets the configured confidence threshold."""
        return len(self.find_secrets(text)) > 0

    def _run_pipeline(self, text: str) -> list[SecretFinding]:
        findings: list[SecretFinding] = []

        # Phase 1: Regex fast path (always run)
        regex_findings = self._detect_regex(text)

        if not self.use_ml:
            findings = [
                f
                for f in regex_findings
                if f.confidence >= self.threshold and not self._is_allowlisted(text, f)
            ]
            return self._deduplicate(findings)

        # Phase 2: Presidio ML analysis + confidence fusion (0.4 * regex + 0.6 * ml)
        detector = self.ml_detector
        target_entities = (
            list(self.entity_types)
            if self.entity_types is not None
            else list(SUPPORTED_SECRET_ENTITIES)
        )
        ml_matches: list[PresidioSpanMatch] = (
            detector.analyze_text(text, entities=target_entities) if detector is not None else []
        )

        # Fuse overlapping regex + Presidio ML findings; validate remaining regex matches via ML
        unmatched_regex: list[SecretFinding] = []
        for finding in regex_findings:
            ml_match = self._find_best_overlap(finding, ml_matches)
            if ml_match is not None and detector is not None:
                ctx_start = max(0, finding.start - 50)
                ctx_end = min(len(text), finding.end + 50)
                cal = detector.calibrate_presidio_match(
                    snippet=finding.snippet,
                    context=text[ctx_start:ctx_end],
                    entity_type=ml_match.entity_type,
                    raw_score=ml_match.score,
                )
                combined = finding.confidence * 0.4 + cal.confidence * 0.6
                if combined >= self.threshold:
                    findings.append(
                        SecretFinding(
                            type=cal.entity_type or finding.type,
                            start=finding.start,
                            end=finding.end,
                            confidence=round(min(1.0, combined), 4),
                            snippet=finding.snippet,
                            detection_method="hybrid",
                        )
                    )
            else:
                unmatched_regex.append(finding)

        if unmatched_regex:
            findings.extend(self._validate_with_ml(text, unmatched_regex))

        # Add ML-only findings (e.g., obfuscated secrets or Presidio-only recognizers)
        for ml_match in ml_matches:
            if self._overlaps_any_finding(ml_match, regex_findings):
                continue
            snippet = text[ml_match.start : ml_match.end]
            ctx_start = max(0, ml_match.start - 50)
            ctx_end = min(len(text), ml_match.end + 50)
            if detector is not None:
                cal = detector.calibrate_presidio_match(
                    snippet=snippet,
                    context=text[ctx_start:ctx_end],
                    entity_type=ml_match.entity_type,
                    raw_score=ml_match.score,
                )
                score = cal.confidence
                entity_type = cal.entity_type
            else:
                score = ml_match.score
                entity_type = ml_match.entity_type

            if score >= self.threshold:
                findings.append(
                    SecretFinding(
                        type=entity_type,
                        start=ml_match.start,
                        end=ml_match.end,
                        confidence=round(min(1.0, score), 4),
                        snippet=snippet,
                        detection_method="ml",
                    )
                )

        # Phase 3: High-entropy & base64-encoded novel secret scan
        already_covered = [(f.start, f.end) for f in regex_findings] + [
            (m.start, m.end) for m in ml_matches
        ]
        entropy_candidates = self._detect_high_entropy(text, skip_spans=already_covered)
        if entropy_candidates:
            findings.extend(self._validate_with_ml(text, entropy_candidates))

        filtered = [
            f
            for f in findings
            if f.confidence >= self.threshold and not self._is_allowlisted(text, f)
        ]
        return self._deduplicate(filtered)

    def redact(self, text: str, placeholder: str = "[REDACTED]") -> str:
        """Detect and redact all findings from ``text`` using ``placeholder:TYPE`` format."""
        findings = self.find_secrets(text)
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

    def anonymize(self, text: str, *, style: str = "entity_redacted") -> str:
        """Detect and anonymize secrets via Presidio AnonymizerEngine (or span fallback).

        Default output format preserves entity structure as ``OPENAI_API_KEY[redacted]``.
        """
        findings = self.find_secrets(text)
        if not findings:
            return text
        detector = self.ml_detector
        if detector is not None:
            return detector.anonymize_text(text, findings, style=style)
        redacted = text
        for finding in sorted(findings, key=lambda f: f.start, reverse=True):
            marker = (
                f"{finding.type}[redacted]"
                if style == "entity_redacted"
                else f"[REDACTED:{finding.type}]"
            )
            redacted = redacted[: finding.start] + marker + redacted[finding.end :]
        return redacted

    _PATTERN_PREFILTERS: dict[str, tuple[str, ...]] = {
        "ANTHROPIC_API_KEY": ("sk-ant-",),
        "OPENAI_API_KEY": ("sk-",),
        "GITHUB_TOKEN": ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"),
        "AWS_ACCESS_KEY": ("akia", "asia"),
        "AWS_SECRET_ACCESS_KEY": ("aws_secret_access_key",),
        "GCP_SERVICE_ACCOUNT": ("aiza", "private_key"),
        "AZURE_API_KEY": ("accountkey=", "azure"),
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

    @staticmethod
    def _find_best_overlap(
        finding: SecretFinding,
        ml_matches: Sequence[PresidioSpanMatch],
    ) -> PresidioSpanMatch | None:
        overlapping = [
            match
            for match in ml_matches
            if not (finding.end <= match.start or match.end <= finding.start)
        ]
        if not overlapping:
            return None
        return max(
            overlapping,
            key=lambda m: (
                m.entity_type == finding.type,
                m.score,
                m.end - m.start,
            ),
        )

    @staticmethod
    def _overlaps_any_finding(
        ml_match: PresidioSpanMatch,
        findings: Sequence[SecretFinding],
    ) -> bool:
        return any(not (ml_match.end <= f.start or f.end <= ml_match.start) for f in findings)

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
            "ANTHROPIC_API_KEY": r"sk-ant-(?:api\d{2}-)?[a-zA-Z0-9_-]{6,}",
            "OPENAI_API_KEY": (
                r"(?:sk-(?:proj-|svcacct-|admin-)[a-zA-Z0-9_-]{6,}|"
                r"sk-(?!ant-)[a-zA-Z0-9]{20,})"
            ),
            "GITHUB_TOKEN": r"(?:gh[pousr]_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{22,})",
            "AWS_ACCESS_KEY": r"(?:AKIA|ASIA)[0-9A-Z]{16}",
            "AWS_SECRET_ACCESS_KEY": (
                r"aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"
            ),
            "GCP_SERVICE_ACCOUNT": (
                r"(?:AIza[0-9A-Za-z_-]{35}|"
                r'"private_key"\s*:\s*"-----BEGIN (?:RSA )?PRIVATE KEY-----)'
            ),
            "AZURE_API_KEY": (
                r"(?:AccountKey=[A-Za-z0-9+/=]{32,}|"
                r"azure[_-]?(?:openai[_-]?)?(?:api[_-]?)?(?:key|secret)"
                r"\s*[:=]\s*['\"]?[a-fA-F0-9A-Za-z+/=_-]{20,}['\"]?)"
            ),
            "SLACK_TOKEN": r"xox[baprs]-[a-zA-Z0-9-]{10,}",
            "PRIVATE_KEY": r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            "GENERIC_API_KEY": r"api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9_-]{20,}['\"]?",
            "PASSWORD": r"pass(?:word|wd)\s*[:=]\s*['\"]?[^\s'\",;]{8,}['\"]?",
            "BEARER_TOKEN": r"bearer\s+[a-zA-Z0-9\-._~+/]{16,}=*",
            "CREDIT_CARD": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        }

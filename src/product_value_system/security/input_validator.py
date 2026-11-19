"""
Adversarial prompt injection detection and LLM input validation (EVO-006).

Implements layered OWASP LLM-01 defenses:
- Pattern-based detection loaded from ``tests/security/injection_patterns.yaml``
  (with built-in fallback patterns if running outside a source checkout)
- Character encoding & smuggling heuristics (null bytes, bidirectional overrides,
  excessive zero-width characters)
- Configurable length and newline bounds
- Structured severity grading (LOW, MEDIUM, HIGH, CRITICAL)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATTERNS_FILE = REPO_ROOT / "tests" / "security" / "injection_patterns.yaml"

# Built-in fallback patterns used when YAML database is unavailable
_BUILTIN_PATTERNS: dict[str, list[dict[str, str]]] = {
    "direct_injection": [
        {
            "pattern": r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
            "severity": "HIGH",
            "description": "Instruction override attempt",
        },
        {
            "pattern": r"(?:output|reveal|print)\s+(?:your|the)?\s*system\s+prompt",
            "severity": "CRITICAL",
            "description": "System prompt extraction",
        },
    ],
    "jailbreak": [
        {
            "pattern": r"\bDAN\s+mode\b",
            "severity": "CRITICAL",
            "description": "Do Anything Now jailbreak",
        },
        {
            "pattern": r"developer\s+mode\s*(?:enabled|activated|:)",
            "severity": "CRITICAL",
            "description": "Developer mode jailbreak",
        },
    ],
    "delimiter_injection": [
        {
            "pattern": r"(?:\r?\n|\\n)\s*System\s*:",
            "severity": "CRITICAL",
            "description": "System delimiter injection",
        },
        {
            "pattern": r"<\|im_start\|>|<\|im_end\|>",
            "severity": "CRITICAL",
            "description": "ChatML delimiter injection",
        },
    ],
    "task_deflection": [
        {
            "pattern": r"switch\s+to\s+(?:creative|unrestricted|admin|debug)\s+mode",
            "severity": "HIGH",
            "description": "Mode-switching task deflection",
        },
    ],
}

# Invisible Unicode smuggling ranges (bidi overrides, zero-width chars, null bytes)
_BIDI_OVERRIDE_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\ufeff]")

SEVERITY_ORDER: dict[str, int] = {
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


class SecurityError(Exception):
    """Raised when an adversarial input violates security policy."""

    def __init__(
        self,
        message: str,
        *,
        risk_level: str = "HIGH",
        matched_patterns: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.risk_level = risk_level
        self.matched_patterns = matched_patterns or []


@dataclass
class ValidationResult:
    """Result of adversarial prompt injection validation."""

    is_valid: bool
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    matched_patterns: list[str] = field(default_factory=list)
    reason: str = "No threats detected"
    categories: list[str] = field(default_factory=list)


@dataclass
class _CompiledRule:
    """Pre-compiled regex rule for fast (<10ms) input scanning."""

    category: str
    pattern_text: str
    regex: re.Pattern[str]
    severity: str
    description: str


class InputValidator:
    """
    Validates LLM inputs for OWASP LLM-01 prompt injection attacks.

    Risk response model:
    - LOW:      Valid, no threats detected
    - MEDIUM:   Valid with warning (logged as security event)
    - HIGH:     Invalid (blocked by Harness with SecurityError)
    - CRITICAL: Invalid (blocked by Harness with SecurityError)
    """

    def __init__(
        self,
        patterns_file: Path | str | None = None,
        *,
        max_length: int = 50_000,
        max_newlines: int = 500,
    ) -> None:
        """
        Initialize prompt injection InputValidator.

        Args:
            patterns_file: Path to YAML attack pattern database (defaults to
                ``tests/security/injection_patterns.yaml``)
            max_length: Maximum allowed input character length before flagging MEDIUM
            max_newlines: Maximum allowed newline count before flagging MEDIUM
        """
        resolved_path = (
            Path(patterns_file) if patterns_file is not None else DEFAULT_PATTERNS_FILE
        )
        self.patterns_file = resolved_path
        self.max_length = max_length
        self.max_newlines = max_newlines
        self.patterns = self._load_patterns(resolved_path)
        self._compiled_rules = self._compile_rules(self.patterns)

    def _load_patterns(self, file_path: Path) -> dict[str, list[dict[str, Any]]]:
        """Load injection patterns from YAML file with built-in fallback."""
        if not file_path.exists():
            return {k: list(v) for k, v in _BUILTIN_PATTERNS.items()}

        loaded = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return {k: list(v) for k, v in _BUILTIN_PATTERNS.items()}

        result: dict[str, list[dict[str, Any]]] = {}
        for category, items in loaded.items():
            if isinstance(items, list):
                result[str(category)] = [item for item in items if isinstance(item, dict)]
        return result

    def _compile_rules(
        self, patterns: dict[str, list[dict[str, Any]]]
    ) -> list[_CompiledRule]:
        """Pre-compile regex rules once during initialization."""
        compiled: list[_CompiledRule] = []
        for category, entries in patterns.items():
            for entry in entries:
                pattern_text = str(entry.get("pattern", "")).strip()
                if not pattern_text:
                    continue
                severity = str(entry.get("severity", "MEDIUM")).upper()
                if severity not in SEVERITY_ORDER:
                    severity = "MEDIUM"
                description = str(entry.get("description", ""))
                compiled.append(
                    _CompiledRule(
                        category=category,
                        pattern_text=pattern_text,
                        regex=re.compile(pattern_text, re.IGNORECASE | re.MULTILINE),
                        severity=severity,
                        description=description,
                    )
                )
        return compiled

    def validate(self, text: str) -> ValidationResult:
        """
        Validate text input for prompt injection and smuggling attacks.

        Args:
            text: User or retrieved text input to inspect

        Returns:
            ValidationResult detailing validity, risk level, and matched rules
        """
        if not text:
            return ValidationResult(
                is_valid=True,
                risk_level="LOW",
                matched_patterns=[],
                reason="Empty input",
                categories=[],
            )

        matched: list[str] = []
        categories: list[str] = []
        max_severity = "LOW"

        # 1. Character encoding & smuggling checks
        if "\x00" in text:
            matched.append("encoding_smuggling: null_byte")
            categories.append("encoding_smuggling")
            max_severity = self._max_severity(max_severity, "HIGH")

        if _BIDI_OVERRIDE_RE.search(text):
            matched.append("encoding_smuggling: unicode_bidi_override")
            categories.append("encoding_smuggling")
            max_severity = self._max_severity(max_severity, "HIGH")

        zero_width_matches = _ZERO_WIDTH_RE.findall(text)
        if len(zero_width_matches) >= 5:
            matched.append("encoding_smuggling: excessive_zero_width_chars")
            categories.append("encoding_smuggling")
            max_severity = self._max_severity(max_severity, "HIGH")

        # 2. Pattern database checks
        for rule in self._compiled_rules:
            if rule.regex.search(text):
                label = f"{rule.category}: {rule.description or rule.pattern_text}"
                matched.append(label)
                if rule.category not in categories:
                    categories.append(rule.category)
                max_severity = self._max_severity(max_severity, rule.severity)

        # 3. Structural heuristics
        if len(text) > self.max_length:
            matched.append(f"heuristic: excessive_length ({len(text)} > {self.max_length})")
            if "heuristic" not in categories:
                categories.append("heuristic")
            max_severity = self._max_severity(max_severity, "MEDIUM")

        newline_count = text.count("\n") + text.count("\\n")
        if newline_count > self.max_newlines:
            matched.append(
                f"heuristic: excessive_newlines ({newline_count} > {self.max_newlines})"
            )
            if "heuristic" not in categories:
                categories.append("heuristic")
            max_severity = self._max_severity(max_severity, "MEDIUM")

        is_valid = max_severity in {"LOW", "MEDIUM"}
        reason = f"Detected: {', '.join(matched)}" if matched else "No threats detected"

        return ValidationResult(
            is_valid=is_valid,
            risk_level=max_severity,
            matched_patterns=matched,
            reason=reason,
            categories=categories,
        )

    def _severity_level(self, severity: str) -> int:
        """Convert severity label to numeric rank."""
        return SEVERITY_ORDER.get(severity.upper(), 0)

    def _max_severity(self, current: str, candidate: str) -> str:
        """Return higher of two severity labels."""
        return (
            candidate
            if self._severity_level(candidate) > self._severity_level(current)
            else current
        )

"""
ML-based secret detection using Microsoft Presidio (when installed) with a calibrated
SecretsML-style feature classifier fallback for lightweight environments (EVO-024).
"""

from __future__ import annotations

import base64
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_FP_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "example",
    "test",
    "fake",
    "dummy",
    "sample",
    "placeholder",
    "mock",
    "fixture",
    "tutorial",
    "documentation",
    "your_api_key",
    "your-api-key",
    "your_password",
    "replace_me",
    "changeme",
    "redacted",
    "todo",
    "not_a_real",
    "not-a-real",
    "demo_only",
)

_FP_PAYLOAD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(example|test|fake|dummy|sample|placeholder|changeme|password123|"
        r"your[_-]|replace[_-]?me|insert[_-]?here|my[_-]?secret|redacted|localhost)",
        re.I,
    ),
    re.compile(r"(.)\1{4,}"),  # 5+ repeated identical characters (e.g. xxxxx, 00000)
    re.compile(r"(012345|123456|abcdef|qwerty)", re.I),
    re.compile(r"^(https?://|/|\\|[A-Za-z]:\\)"),
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
)

_SECRET_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "api-key",
    "api key",
    "openai",
    "anthropic",
    "claude",
    "github",
    "aws",
    "slack",
    "secret",
    "token",
    "bearer",
    "authorization",
    "password",
    "passwd",
    "private",
    "credential",
    "prod",
    "production",
    "live",
    "encoded",
    "stripe",
    "vault",
    "hmac",
    "signing",
)

_BENIGN_HASH_KEYWORDS: tuple[str, ...] = (
    "commit",
    "sha256",
    "sha1",
    "sha-256",
    "checksum",
    "digest",
    "etag",
    "md5",
    "fingerprint",
    "git",
    "docker",
    "image_tag",
    "uuid",
    "trace_id",
    "span_id",
    "correlation_id",
    "build_id",
)

_KNOWN_PREFIX_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ANTHROPIC_API_KEY", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("OPENAI_API_KEY", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS_ACCESS_KEY", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("SLACK_TOKEN", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("BEARER_TOKEN", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
)

_DECODED_SECRET_HINT_RE = re.compile(
    r"(sk-[A-Za-z0-9]|ghp_|AKIA|secret|api[_-]?key|password|token|bearer|private|"
    r"[a-zA-Z0-9_-]+:[a-zA-Z0-9_!@#$%^&*-]{6,})",
    re.I,
)


_CODE_VAR_NAMES = frozenset(
    {"password", "passwd", "secret", "token", "api_key", "apikey", "none", "null", "true", "false"}
)
_CODE_EXPR_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+(?:\([^)]*\))?$|"
    r"^[A-Za-z_][A-Za-z0-9_]*\([^)]*\)$"
)


_FILESYSTEM_PATH_RE = re.compile(
    r"^(?:\.{0,2}/)?[a-z][a-z0-9_-]*(?:/[a-z][a-z0-9_.-]*){2,}$"
)
_PASCAL_CASE_IDENT_RE = re.compile(r"^[A-Z]{1,4}[a-z]{2,}(?:[A-Z]{1,4}[a-z]{2,}){2,}$")
_PYTHON_KWARG_CONST_RE = re.compile(
    r"^([a-z][a-z0-9_]*)=(_?[A-Z][A-Z0-9_]{2,}|[A-Z]{1,4}[a-z]{2,}(?:[A-Z]{1,4}[a-z]{2,})+)$"
)
_ENV_CONFIG_ASSIGN_RE = re.compile(r"^([A-Z][A-Z0-9_]{2,})=([a-z0-9_.-]{1,16})$")
_SECRET_LHS_WORDS = ("secret", "token", "key", "pass", "pwd", "auth", "cred")


def _is_unquoted_code_reference(snippet: str, payload: str) -> bool:
    """Return True if an unquoted assignment RHS is a code variable or call expression."""
    m = re.match(
        r"^(?:aws_secret_access_key|api[_-]?key|apikey|pass(?:word|wd)|token|secret)\s*[:=]\s*(.+)$",
        snippet.strip(),
        re.I,
    )
    if not m:
        return False
    rhs = m.group(1).strip().rstrip(",;)")
    if rhs and rhs[0] in {'"', "'"}:
        return False
    if rhs.lower() in _CODE_VAR_NAMES or payload.lower() in _CODE_VAR_NAMES:
        return True
    # Match dotted/call expressions like redis_cfg.get(...) or self.password
    if _CODE_EXPR_RE.match(rhs) or ".get(" in rhs or "getenv(" in rhs or "environ[" in rhs:
        return True
    return False


def calculate_shannon_entropy(value: str) -> float:
    """Compute Shannon entropy in bits per character."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def extract_secret_payload(snippet: str) -> str:
    """Strip key/prefix wrappers (e.g. ``api_key=`` or ``Bearer ``) to inspect the raw value."""
    cleaned = snippet.strip()
    kv_match = re.match(
        r"^(?:aws_secret_access_key|api[_-]?key|apikey|pass(?:word|wd)|token|secret)\s*[:=]\s*(.+)$",
        cleaned,
        re.I,
    )
    if kv_match:
        cleaned = kv_match.group(1).strip()
    elif cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()

    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def passes_luhn_check(candidate: str) -> bool:
    """Validate payment card digit string using the Luhn checksum algorithm."""
    digits = re.sub(r"[\s-]", "", candidate)
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    if len(set(digits)) <= 2:
        return False
    if digits.startswith(("12345678", "0000", "1111")):
        return False

    total = 0
    for idx, ch in enumerate(reversed(digits)):
        num = int(ch)
        if idx % 2 == 1:
            num *= 2
            if num > 9:
                num -= 9
        total += num
    return total % 10 == 0


def inspect_base64_secret(payload: str) -> tuple[bool, str]:
    """Return ``(is_encoded_secret, decoded_preview)`` if payload is a base64-encoded secret."""
    compact = payload.strip()
    if len(compact) < 16 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", compact):
        return False, ""
    if compact.isalpha() and (compact.islower() or compact.isupper()):
        return False, ""

    padded = compact + "=" * ((4 - len(compact) % 4) % 4)
    try:
        raw = base64.b64decode(padded.replace("-", "+").replace("_", "/"), validate=True)
    except Exception:
        return False, ""
    if len(raw) < 10:
        return False, ""

    printable = sum(1 for b in raw if 32 <= b <= 126 or b in (9, 10, 13))
    if printable / len(raw) < 0.85:
        return False, ""

    decoded = raw.decode("utf-8", errors="ignore")
    if _DECODED_SECRET_HINT_RE.search(decoded):
        return True, decoded[:64]
    return False, ""


@dataclass
class MLAnalysisResult:
    """Result returned by MLSecretDetector contextual validation."""

    entity_type: str
    confidence: float
    is_real_secret: bool
    backend: str = "ml_classifier"
    reasons: list[str] = field(default_factory=list)


class MLSecretDetector:
    """ML-based secret detector backed by Microsoft Presidio with local ML classifier fallback."""

    def __init__(
        self,
        *,
        spacy_model: str = "en_core_web_lg",
        confidence_threshold: float = 0.7,
        enable_presidio: bool = True,
        analyzer: Any | None = None,
        anonymizer: Any | None = None,
    ) -> None:
        self.spacy_model = spacy_model
        self.confidence_threshold = confidence_threshold
        self.analyzer: Any | None = analyzer
        self.anonymizer: Any | None = anonymizer
        self.backend: str = "presidio" if analyzer is not None else "ml_classifier"

        if enable_presidio and self.analyzer is None:
            self._init_presidio()

    def _init_presidio(self) -> None:
        """Attempt to initialize Microsoft Presidio AnalyzerEngine + custom secret recognizers."""
        try:
            from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            nlp_config = {
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": self.spacy_model}],
            }
            nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
            registry = RecognizerRegistry()
            registry.load_predefined_recognizers()
            self.analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine,
                registry=registry,
                supported_languages=["en"],
            )
            self._register_custom_recognizers()
            self.backend = "presidio"
            logger.info("Initialized Microsoft Presidio secret analyzer (%s)", self.spacy_model)
        except Exception as exc:
            logger.debug(
                "Presidio/spaCy unavailable (%s); using built-in ML feature classifier.",
                exc,
            )
            self.analyzer = None
            self.backend = "ml_classifier"

        try:
            from presidio_anonymizer import AnonymizerEngine

            self.anonymizer = AnonymizerEngine()
        except Exception:
            self.anonymizer = None

    def _register_custom_recognizers(self) -> None:
        """Register domain-specific API key & credential PatternRecognizers with Presidio."""
        if self.analyzer is None:
            return
        from presidio_analyzer import Pattern, PatternRecognizer

        specs = [
            ("OPENAI_API_KEY", r"sk-[a-zA-Z0-9]{48}", 0.9, ["openai", "api", "key", "token"]),
            (
                "ANTHROPIC_API_KEY",
                r"sk-ant-[a-zA-Z0-9_-]{20,}",
                0.9,
                ["anthropic", "claude", "api", "key"],
            ),
            ("GITHUB_TOKEN", r"ghp_[a-zA-Z0-9]{36}", 0.9, ["github", "token", "pat"]),
            ("AWS_ACCESS_KEY", r"AKIA[0-9A-Z]{16}", 0.9, ["aws", "access", "key", "iam"]),
            ("SLACK_TOKEN", r"xox[baprs]-[a-zA-Z0-9-]{10,}", 0.85, ["slack", "token", "bot"]),
            (
                "BEARER_TOKEN",
                r"bearer\s+[a-zA-Z0-9\-._~+/]+=*",
                0.85,
                ["authorization", "bearer", "jwt"],
            ),
            (
                "GENERIC_API_KEY",
                r"api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9_-]{20,}['\"]?",
                0.8,
                ["api", "key", "secret"],
            ),
            (
                "PASSWORD",
                r"pass(?:word|wd)\s*[:=]\s*['\"]?[^\s'\"]{8,}['\"]?",
                0.75,
                ["password", "passwd", "secret", "login"],
            ),
        ]
        for entity_name, regex, score, context_words in specs:
            recognizer = PatternRecognizer(
                supported_entity=entity_name,
                patterns=[Pattern(name=entity_name.lower(), regex=regex, score=score)],
                context=context_words,
            )
            self.analyzer.registry.add_recognizer(recognizer)

    def analyze_context(
        self,
        snippet: str,
        context: str,
        entity_type: str = "API_KEY",
    ) -> MLAnalysisResult:
        """Validate a candidate secret snippet within its surrounding context."""
        if self.analyzer is not None:
            presidio_res = self._analyze_with_presidio(snippet, context, entity_type)
            if presidio_res is not None:
                return presidio_res

        return self._score_with_ml_classifier(snippet, context, entity_type)

    def _analyze_with_presidio(
        self,
        snippet: str,
        context: str,
        entity_type: str,
    ) -> MLAnalysisResult | None:
        assert self.analyzer is not None
        entities = list(
            dict.fromkeys(
                [
                    entity_type,
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "GITHUB_TOKEN",
                    "AWS_ACCESS_KEY",
                    "SLACK_TOKEN",
                    "GENERIC_API_KEY",
                    "API_KEY",
                    "PASSWORD",
                    "BEARER_TOKEN",
                    "CREDIT_CARD",
                    "CRYPTO",
                ]
            )
        )
        try:
            results = self.analyzer.analyze(text=context, entities=entities, language="en")
        except Exception as exc:
            logger.debug("Presidio analyze failed (%s); falling back to ML classifier.", exc)
            return None

        lower_context = context.lower()
        payload = extract_secret_payload(snippet)
        has_fp_cue = any(kw in lower_context for kw in _FP_CONTEXT_KEYWORDS) or any(
            pat.search(payload) for pat in _FP_PAYLOAD_PATTERNS
        )

        for result in results:
            matched_text = context[result.start : result.end]
            if snippet in matched_text or matched_text in snippet or payload in matched_text:
                confidence = float(result.score)
                reasons = [f"presidio:{result.entity_type}({confidence:.2f})"]
                if has_fp_cue:
                    confidence *= 0.45
                    reasons.append("context_fp_penalty")
                confidence = max(0.0, min(1.0, confidence))
                return MLAnalysisResult(
                    entity_type=str(result.entity_type),
                    confidence=round(confidence, 4),
                    is_real_secret=confidence >= self.confidence_threshold,
                    backend="presidio",
                    reasons=reasons,
                )

        # Presidio found nothing; let local ML classifier inspect entropy/base64/context
        return None

    def _score_with_ml_classifier(
        self,
        snippet: str,
        context: str,
        entity_type: str,
    ) -> MLAnalysisResult:
        """SecretsML-inspired feature classifier combining entropy, format, base64, and context."""
        payload = extract_secret_payload(snippet)
        lower_context = context.lower()
        reasons: list[str] = []

        # 0. Reject unquoted code variable / call references (e.g. password=password)
        if _is_unquoted_code_reference(snippet, payload):
            return MLAnalysisResult(
                entity_type=entity_type,
                confidence=0.1,
                is_real_secret=False,
                backend="ml_classifier",
                reasons=["unquoted_code_reference"],
            )

        # 0b. Reject lowercase multi-segment repository file paths in entropy-only scans
        if entity_type == "UNKNOWN_SECRET" and _FILESYSTEM_PATH_RE.fullmatch(payload):
            return MLAnalysisResult(
                entity_type=entity_type,
                confidence=0.1,
                is_real_secret=False,
                backend="ml_classifier",
                reasons=["filesystem_path"],
            )

        # 0c. Reject pure PascalCase code identifiers, non-secret Python kwargs, and env config
        if entity_type == "UNKNOWN_SECRET":
            if _PASCAL_CASE_IDENT_RE.fullmatch(payload):
                return MLAnalysisResult(
                    entity_type=entity_type,
                    confidence=0.1,
                    is_real_secret=False,
                    backend="ml_classifier",
                    reasons=["pascal_case_identifier"],
                )
            kw_match = _PYTHON_KWARG_CONST_RE.fullmatch(payload)
            if kw_match and not any(w in kw_match.group(1) for w in _SECRET_LHS_WORDS):
                return MLAnalysisResult(
                    entity_type=entity_type,
                    confidence=0.1,
                    is_real_secret=False,
                    backend="ml_classifier",
                    reasons=["python_kwarg_constant"],
                )
            env_match = _ENV_CONFIG_ASSIGN_RE.fullmatch(payload)
            if env_match and not any(
                w in env_match.group(1).lower() for w in _SECRET_LHS_WORDS
            ):
                return MLAnalysisResult(
                    entity_type=entity_type,
                    confidence=0.1,
                    is_real_secret=False,
                    backend="ml_classifier",
                    reasons=["non_secret_env_config"],
                )

        entropy = calculate_shannon_entropy(payload)
        char_classes = sum(
            [
                any(c.islower() for c in payload),
                any(c.isupper() for c in payload),
                any(c.isdigit() for c in payload),
                any(not c.isalnum() for c in payload),
            ]
        )

        fp_context_hits = sum(1 for kw in _FP_CONTEXT_KEYWORDS if kw in lower_context)
        fp_payload = any(pat.search(payload) for pat in _FP_PAYLOAD_PATTERNS)
        secret_context_hits = sum(1 for kw in _SECRET_CONTEXT_KEYWORDS if kw in lower_context)
        benign_hash_hit = any(kw in lower_context for kw in _BENIGN_HASH_KEYWORDS)

        # 1. Credit card validation with Luhn checksum
        if entity_type == "CREDIT_CARD":
            if not passes_luhn_check(payload) or fp_context_hits > 0:
                return MLAnalysisResult(
                    entity_type="CREDIT_CARD",
                    confidence=0.15,
                    is_real_secret=False,
                    backend="ml_classifier",
                    reasons=["failed_luhn_or_fp_context"],
                )
            score = 0.94 if (secret_context_hits > 0 or "card" in lower_context) else 0.86
            return MLAnalysisResult(
                entity_type="CREDIT_CARD",
                confidence=score,
                is_real_secret=score >= self.confidence_threshold,
                backend="ml_classifier",
                reasons=["luhn_valid_card"],
            )

        # 2. Known high-precision provider signatures
        detected_type = entity_type
        matched_prefix = False
        for known_type, pattern in _KNOWN_PREFIX_PATTERNS:
            if pattern.search(snippet) or pattern.search(payload):
                detected_type = known_type
                matched_prefix = True
                break

        if matched_prefix:
            score = 0.96
            reasons.append(f"signature:{detected_type}")
            if fp_payload:
                score *= 0.25
                reasons.append("placeholder_payload_penalty")
            elif fp_context_hits > 0:
                score *= 0.42
                reasons.append("example_context_penalty")
            elif entropy < 3.0 and detected_type != "PRIVATE_KEY":
                score *= 0.55
                reasons.append("low_entropy_penalty")
            score = max(0.0, min(1.0, score))
            return MLAnalysisResult(
                entity_type=detected_type,
                confidence=round(score, 4),
                is_real_secret=score >= self.confidence_threshold,
                backend="ml_classifier",
                reasons=reasons,
            )

        # 3. Base64-encoded secret payload inspection
        is_b64_secret, _ = inspect_base64_secret(payload)
        if is_b64_secret:
            reasons.append("base64_decoded_secret_payload")
            if fp_context_hits > 0 and "encoded" not in lower_context:
                score = 0.32
                reasons.append("fp_context_penalty")
            elif fp_context_hits > 0:
                score = 0.45
                reasons.append("fp_context_penalty")
            else:
                score = 0.92
            return MLAnalysisResult(
                entity_type="ENCODED_SECRET" if entity_type == "UNKNOWN_SECRET" else entity_type,
                confidence=score,
                is_real_secret=score >= self.confidence_threshold,
                backend="ml_classifier",
                reasons=reasons,
            )

        # 4. Hex-encoded secret detection (32+ hex chars with secret context, not git/checksum)
        if (
            len(payload) >= 32
            and re.fullmatch(r"[0-9a-fA-F]+", payload)
            and entropy >= 3.2
            and not fp_payload
        ):
            if benign_hash_hit or fp_context_hits > 0:
                return MLAnalysisResult(
                    entity_type=entity_type,
                    confidence=0.18,
                    is_real_secret=False,
                    backend="ml_classifier",
                    reasons=["benign_hash_or_fp_context"],
                )
            if secret_context_hits > 0 or len(payload) >= 64:
                score = 0.89 if secret_context_hits > 0 else 0.78
                return MLAnalysisResult(
                    entity_type="HEX_SECRET" if entity_type == "UNKNOWN_SECRET" else entity_type,
                    confidence=score,
                    is_real_secret=score >= self.confidence_threshold,
                    backend="ml_classifier",
                    reasons=["high_entropy_hex_secret"],
                )

        # 5. Calibrated logistic classifier over entropy, char diversity, length, and context
        z = -3.8
        z += 1.25 * entropy
        z += 0.65 * char_classes
        z += 1.15 * min(secret_context_hits, 2)
        if len(payload) >= 24:
            z += 0.80
        elif len(payload) >= 12:
            z += 0.35
        else:
            z -= 0.80

        if fp_payload:
            z -= 3.80
            reasons.append("placeholder_payload")
        if fp_context_hits > 0:
            z -= 2.60 * min(fp_context_hits, 2)
            reasons.append("fp_context_keywords")
        if benign_hash_hit:
            z -= 3.50
            reasons.append("benign_hash_context")
        if entity_type == "UNKNOWN_SECRET" and secret_context_hits == 0 and entropy < 4.7:
            z -= 2.20
            reasons.append("no_secret_context_for_entropy_match")

        probability = 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, z))))
        if probability >= self.confidence_threshold:
            reasons.append(f"ml_score(H={entropy:.2f},cls={char_classes})")
        return MLAnalysisResult(
            entity_type=detected_type,
            confidence=round(probability, 4),
            is_real_secret=probability >= self.confidence_threshold,
            backend="ml_classifier",
            reasons=reasons,
        )

    def anonymize_text(self, text: str, findings: list[Any]) -> str:
        """Anonymize detected secret spans using Presidio Anonymizer or span replacement."""
        if not findings:
            return text
        if self.anonymizer is not None:
            try:
                from presidio_analyzer import RecognizerResult

                presidio_results = [
                    RecognizerResult(
                        entity_type=f.type,
                        start=f.start,
                        end=f.end,
                        score=f.confidence,
                    )
                    for f in findings
                ]
                return str(
                    self.anonymizer.anonymize(text=text, analyzer_results=presidio_results).text
                )
            except Exception as exc:
                logger.debug(
                    "Presidio anonymizer unavailable, falling back to span redact: %s",
                    exc,
                )

        redacted = text
        for finding in sorted(findings, key=lambda f: f.start, reverse=True):
            redacted = (
                redacted[: finding.start]
                + f"[REDACTED:{finding.type}]"
                + redacted[finding.end :]
            )
        return redacted

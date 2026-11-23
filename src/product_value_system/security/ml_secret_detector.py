"""
ML-based secret detection using Microsoft Presidio (AnalyzerEngine + AnonymizerEngine)
with custom AI/cloud/crypto secret & obfuscation recognizers and a calibrated
SecretsML-style feature classifier fallback for lightweight environments (EVO-024 / PROD-001).
"""

from __future__ import annotations

import base64
import importlib.util
import logging
import math
import re
import urllib.parse
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_PRESIDIO_AVAILABLE: bool = (
    importlib.util.find_spec("presidio_analyzer") is not None
    and importlib.util.find_spec("presidio_anonymizer") is not None
)

# Process-wide cache of lean spaCy NLP engines to avoid paying ~800ms cold load per detector
_SPACY_NLP_ENGINE_CACHE: dict[str, Any] = {}

SUPPORTED_SECRET_ENTITIES: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "GCP_SERVICE_ACCOUNT",
    "GITHUB_TOKEN",
    "AZURE_API_KEY",
    "SLACK_TOKEN",
    "PRIVATE_KEY",
    "BEARER_TOKEN",
    "GENERIC_API_KEY",
    "PASSWORD",
    "CREDIT_CARD",
    "ENCODED_SECRET",
)

_FP_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "example",
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
    "unit test",
    "test fixture",
)

_STRONG_FP_PAYLOAD_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(fake|dummy|sample|placeholder|changeme|password123|"
        r"your[_-]|replace[_-]?me|insert[_-]?here|my[_-]?secret|redacted|localhost)",
        re.I,
    ),
    # 5+ repeated alnum chars (e.g. xxxxx, 00000); preserves PEM '-----' headers
    re.compile(r"([A-Za-z0-9])\1{4,}"),
    re.compile(r"^(https?://|/|\\|[A-Za-z]:\\)"),
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),
)

_SEQUENTIAL_FP_PAYLOAD_RE = re.compile(r"(012345|123456|abcdef|qwerty)", re.I)

# Lowercase example/test markers used for synthetic passwords & documentation placeholders
_SOFT_FP_PAYLOAD_RE = re.compile(r"(?:^|[_-])(?:example|test_fake|test_key|example\d+)", re.I)

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
    "gcp",
    "google",
    "azure",
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
    "key",
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
    ("ANTHROPIC_API_KEY", re.compile(r"sk-ant-(?:api\d{2}-)?[A-Za-z0-9_-]{6,}")),
    ("OPENAI_API_KEY", re.compile(r"sk-(?:proj-|svcacct-|admin-)[A-Za-z0-9_-]{6,}")),
    ("OPENAI_API_KEY", re.compile(r"sk-(?!ant-)[A-Za-z0-9]{20,}")),
    ("GITHUB_TOKEN", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{22,})")),
    ("AWS_ACCESS_KEY", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("GCP_SERVICE_ACCOUNT", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    (
        "GCP_SERVICE_ACCOUNT",
        re.compile(r'"private_key"\s*:\s*"-----BEGIN (?:RSA )?PRIVATE KEY-----'),
    ),
    ("AZURE_API_KEY", re.compile(r"AccountKey=[A-Za-z0-9+/=]{32,}")),
    ("SLACK_TOKEN", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("PRIVATE_KEY", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("BEARER_TOKEN", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+")),
)

_DECODED_SECRET_HINT_RE = re.compile(
    r"(sk-(?:proj-|ant-|svcacct-|admin-)?[A-Za-z0-9]{4,}|gh[pousr]_[A-Za-z0-9]{6,}|"
    r"AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|AccountKey=|secret|api[_-]?key|"
    r"password|token|bearer|private|[a-zA-Z0-9_-]+:[a-zA-Z0-9_!@#$%^&*-]{6,})",
    re.I,
)

# Obfuscation detection regexes (strictly linear / bounded to prevent ReDoS)
_SPACED_SINGLE_CHAR_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"s\s+k\s*[-_\s]\s*(?:p\s+r\s+o\s+j|a\s+n\s+t)|"
    r"A\s+K\s+I\s+A|"
    r"g\s+h\s+p\s*_"
    r")"
    r"(?:[\s_-]+[A-Za-z0-9]){5,80}"
)
_SPACED_WORD_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:sk[\s._]+(?:proj|ant|svcacct|admin)[\s._]+[A-Za-z0-9_-]{6,64}|"
    r"ghp[\s._]+[A-Za-z0-9]{16,48}|"
    r"AKIA[\s._]+[0-9A-Z]{4}(?:[\s._]*[0-9A-Z]{4}){3})"
    r"(?![A-Za-z0-9_-])"
)
_HEX_ESCAPE_SPAN_RE = re.compile(
    r"(?:[A-Za-z0-9_-]{0,24}\\x[0-9a-fA-F]{2}[A-Za-z0-9_-]{0,24}){1,64}"
)
_STRING_CONCAT_RE = re.compile(
    r"""(['"])([^'"\n]{2,64})\1(?:\s*\+\s*(['"])([^'"\n]{2,64})\3){1,5}"""
)
_URL_ENCODED_SPAN_RE = re.compile(
    r"(?:sk|ghp|AKIA|AIza)[A-Za-z0-9_%-]{10,96}",
)
_B64_QUOTED_OR_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/]{16,128}={0,2}(?![A-Za-z0-9+/=_-])"
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
        r"^(?:aws_secret_access_key|azure[_-]?(?:openai[_-]?)?(?:api[_-]?)?(?:key|secret)|"
        r"api[_-]?key|apikey|pass(?:word|wd)|token|secret)\s*[:=]\s*(.+)$",
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


def _has_fp_payload(payload: str, entity_type: str = "") -> bool:
    """Return True when the payload itself clearly looks like a synthetic placeholder."""
    if any(pat.search(payload) for pat in _STRONG_FP_PAYLOAD_PATTERNS):
        return True
    # High-precision provider prefixes (sk-, AKIA, ghp_) should not be discarded solely for
    # sequential digits/hex or uppercase EXAMPLE suffixes unless surrounded by FP context.
    if entity_type in {"AWS_ACCESS_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"}:
        if re.search(r"sk-(?:example|test\d*example)", payload, re.I):
            return True
        return False
    if _SEQUENTIAL_FP_PAYLOAD_RE.search(payload):
        return True
    return bool(_SOFT_FP_PAYLOAD_RE.search(payload))


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
        r"^(?:aws_secret_access_key|azure[_-]?(?:openai[_-]?)?(?:api[_-]?)?(?:key|secret)|"
        r"AccountKey|api[_-]?key|apikey|pass(?:word|wd)|token|secret)\s*[:=]\s*(.+)$",
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


def _classify_decoded_secret(decoded: str) -> str | None:
    """Classify a decoded/de-obfuscated string against known provider secret formats."""
    for entity_type, pattern in _KNOWN_PREFIX_PATTERNS:
        if pattern.search(decoded):
            return entity_type
    return None


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


@dataclass(frozen=True)
class PresidioSpanMatch:
    """Normalized span finding emitted by Presidio AnalyzerEngine or obfuscation scanner."""

    entity_type: str
    start: int
    end: int
    score: float
    backend: str = "presidio"
    obfuscation_type: str | None = None


def detect_obfuscated_secrets(text: str) -> list[PresidioSpanMatch]:
    """Detect spaced, hex-escaped, string-concatenated, URL-encoded, and base64 secrets."""
    if not text:
        return []

    matches: list[PresidioSpanMatch] = []
    lower = text.lower()

    # 1. Spaced / character-separated credentials (single-char or word-separated forms)
    if "sk" in lower or "s k" in lower or "a k i a" in lower or "g h p" in lower or "akia" in lower:
        for pat in (_SPACED_SINGLE_CHAR_RE, _SPACED_WORD_SECRET_RE):
            for m in pat.finditer(text):
                raw = m.group(0)
                compact = re.sub(r"\s+", "", raw)
                # Normalize space-separated prefix delimiters into standard hyphenated form
                normalized = re.sub(
                    r"^(sk)[\s._-]+(proj|ant|svcacct|admin)[\s._-]+",
                    r"\1-\2-",
                    raw.strip(),
                    flags=re.I,
                )
                normalized = re.sub(r"\s+", "", normalized)
                if normalized.lower().startswith("skproj") and not normalized.lower().startswith(
                    "sk-proj-"
                ):
                    normalized = "sk-proj-" + normalized[6:]
                elif normalized.lower().startswith("skant") and not normalized.lower().startswith(
                    "sk-ant-"
                ):
                    normalized = "sk-ant-" + normalized[5:]
                entity = _classify_decoded_secret(normalized) or _classify_decoded_secret(compact)
                if entity is not None:
                    matches.append(
                        PresidioSpanMatch(
                            entity_type=entity,
                            start=m.start(),
                            end=m.end(),
                            score=0.88,
                            backend="presidio",
                            obfuscation_type="spaced",
                        )
                    )

    # 2. Hex-escape encoded secrets (backslash-x byte escapes inside credential spans)
    if "\\x" in text:
        for m in _HEX_ESCAPE_SPAN_RE.finditer(text):
            raw = m.group(0)
            decoded = re.sub(
                r"\\x([0-9a-fA-F]{2})",
                lambda hx: chr(int(hx.group(1), 16)),
                raw,
            )
            entity = _classify_decoded_secret(decoded)
            if entity is not None:
                matches.append(
                    PresidioSpanMatch(
                        entity_type=entity,
                        start=m.start(),
                        end=m.end(),
                        score=0.90,
                        backend="presidio",
                        obfuscation_type="hex_escape",
                    )
                )

    # 3. String-concatenated credentials split across adjacent quoted literals
    if "+" in text and ('"' in text or "'" in text):
        for m in _STRING_CONCAT_RE.finditer(text):
            raw = m.group(0)
            parts = re.findall(r"""(['"])([^'"\n]+)\1""", raw)
            joined = "".join(val for _, val in parts)
            # Ignore pure regex/pattern definitions containing regex metacharacters
            if any(ch in joined for ch in "[]()*?^$\\"):
                continue
            entity = _classify_decoded_secret(joined)
            if entity is not None:
                matches.append(
                    PresidioSpanMatch(
                        entity_type=entity,
                        start=m.start(),
                        end=m.end(),
                        score=0.90,
                        backend="presidio",
                        obfuscation_type="concatenated",
                    )
                )

    # 4. Percent/URL-encoded credentials
    if "%" in text:
        for m in _URL_ENCODED_SPAN_RE.finditer(text):
            raw = m.group(0)
            if "%" not in raw:
                continue
            decoded = urllib.parse.unquote(raw)
            if decoded != raw:
                entity = _classify_decoded_secret(decoded)
                if entity is not None:
                    matches.append(
                        PresidioSpanMatch(
                            entity_type=entity,
                            start=m.start(),
                            end=m.end(),
                            score=0.88,
                            backend="presidio",
                            obfuscation_type="url_encoded",
                        )
                    )

    # 5. Base64-encoded credentials (e.g. inline base64 decode payloads)
    if "b64" in lower or "base64" in lower or "=" in text:
        for m in _B64_QUOTED_OR_TOKEN_RE.finditer(text):
            token = m.group(0)
            is_b64, decoded = inspect_base64_secret(token)
            if is_b64:
                entity = _classify_decoded_secret(decoded) or "ENCODED_SECRET"
                matches.append(
                    PresidioSpanMatch(
                        entity_type=entity,
                        start=m.start(),
                        end=m.end(),
                        score=0.90,
                        backend="presidio",
                        obfuscation_type="base64",
                    )
                )

    return matches


def _load_fast_spacy_nlp_engine(preferred_model: str = "en_core_web_sm") -> tuple[Any, str]:
    """Load and cache a lean spaCy NlpEngine (~10ms on 10KB) supporting Presidio context boost."""
    import spacy
    from presidio_analyzer.nlp_engine import SpacyNlpEngine
    from spacy.language import Language

    if not Language.has_factory("pvs_fast_lower_lemma"):

        @Language.component("pvs_fast_lower_lemma")
        def _pvs_fast_lower_lemma(doc: Any) -> Any:
            for token in doc:
                if not token.lemma_:
                    token.lemma_ = token.lower_
            return doc

    candidates = list(dict.fromkeys([preferred_model, "en_core_web_sm", "en_core_web_lg"]))
    last_error: Exception | None = None
    for model_name in candidates:
        if model_name in _SPACY_NLP_ENGINE_CACHE:
            return _SPACY_NLP_ENGINE_CACHE[model_name], model_name
        try:
            nlp = spacy.load(
                model_name,
                disable=["tok2vec", "tagger", "parser", "attribute_ruler", "lemmatizer", "ner"],
            )
            nlp.add_pipe("pvs_fast_lower_lemma")
            nlp_engine: Any = SpacyNlpEngine(
                models=[{"lang_code": "en", "model_name": model_name}]
            )
            nlp_engine.nlp = {"en": nlp}
            _SPACY_NLP_ENGINE_CACHE[model_name] = nlp_engine
            return nlp_engine, model_name
        except Exception as exc:
            last_error = exc

    raise RuntimeError(f"No compatible spaCy English model available ({candidates}): {last_error}")


def _build_obfuscated_presidio_recognizer() -> Any:
    """Create a Presidio EntityRecognizer detecting spaced, hex, concatenated & base64 secrets."""
    from presidio_analyzer import EntityRecognizer, RecognizerResult

    class _PresidioObfuscatedSecretRecognizer(EntityRecognizer):
        def __init__(self) -> None:
            super().__init__(
                supported_entities=[
                    "OPENAI_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "AWS_ACCESS_KEY",
                    "GCP_SERVICE_ACCOUNT",
                    "GITHUB_TOKEN",
                    "AZURE_API_KEY",
                    "ENCODED_SECRET",
                ],
                supported_language="en",
                name="ObfuscatedSecretRecognizer",
            )

        def load(self) -> None:
            return None

        def analyze(
            self,
            text: str,
            entities: list[str],
            nlp_artifacts: Any = None,
        ) -> list[Any]:
            allowed = set(entities) if entities else set(self.supported_entities)
            results: list[Any] = []
            for hit in detect_obfuscated_secrets(text):
                if hit.entity_type in allowed:
                    results.append(
                        RecognizerResult(
                            entity_type=hit.entity_type,
                            start=hit.start,
                            end=hit.end,
                            score=hit.score,
                        )
                    )
            return results

    return _PresidioObfuscatedSecretRecognizer()


def build_presidio_custom_recognizers() -> list[Any]:
    """Build all custom Presidio recognizers (OpenAI, Anthropic, AWS, GCP, GitHub, Azure + more)."""
    from presidio_analyzer import Pattern, PatternRecognizer

    recognizers: list[Any] = [
        PatternRecognizer(
            supported_entity="OPENAI_API_KEY",
            name="OpenAIKeyRecognizer",
            patterns=[
                Pattern(
                    name="openai_project_key",
                    regex=r"sk-(?:proj-|svcacct-|admin-)[a-zA-Z0-9_-]{6,}",
                    score=0.85,
                ),
                Pattern(
                    name="openai_legacy_key",
                    regex=r"sk-(?!ant-)[a-zA-Z0-9]{20,}",
                    score=0.85,
                ),
            ],
            context=["openai", "api", "key", "token", "secret"],
        ),
        PatternRecognizer(
            supported_entity="ANTHROPIC_API_KEY",
            name="AnthropicKeyRecognizer",
            patterns=[
                Pattern(
                    name="anthropic_key",
                    regex=r"sk-ant-(?:api\d{2}-)?[a-zA-Z0-9_-]{6,}",
                    score=0.85,
                ),
            ],
            context=["anthropic", "claude", "api", "key", "token"],
        ),
        PatternRecognizer(
            supported_entity="AWS_ACCESS_KEY",
            name="AWSAccessKeyRecognizer",
            patterns=[
                Pattern(name="aws_access_key_id", regex=r"(?:AKIA|ASIA)[0-9A-Z]{16}", score=0.85),
            ],
            context=["aws", "access", "key", "iam", "amazon"],
        ),
        PatternRecognizer(
            supported_entity="AWS_SECRET_ACCESS_KEY",
            name="AWSSecretKeyRecognizer",
            patterns=[
                Pattern(
                    name="aws_secret_access_key",
                    regex=r"aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?",
                    score=0.85,
                ),
            ],
            context=["aws", "secret", "access", "iam"],
        ),
        PatternRecognizer(
            supported_entity="GCP_SERVICE_ACCOUNT",
            name="GCPServiceAccountRecognizer",
            patterns=[
                Pattern(
                    name="gcp_private_key_json",
                    regex=r'"private_key"\s*:\s*"-----BEGIN (?:RSA )?PRIVATE KEY-----',
                    score=0.95,
                ),
                Pattern(
                    name="gcp_api_key",
                    regex=r"AIza[0-9A-Za-z_-]{35}",
                    score=0.88,
                ),
            ],
            context=["gcp", "google", "service_account", "project_id", "private_key", "firebase"],
        ),
        PatternRecognizer(
            supported_entity="GITHUB_TOKEN",
            name="GitHubTokenRecognizer",
            patterns=[
                Pattern(
                    name="github_classic_token",
                    regex=r"gh[pousr]_[a-zA-Z0-9]{20,}",
                    score=0.85,
                ),
                Pattern(
                    name="github_fine_grained_pat",
                    regex=r"github_pat_[a-zA-Z0-9_]{22,}",
                    score=0.88,
                ),
            ],
            context=["github", "token", "pat", "git", "key"],
        ),
        PatternRecognizer(
            supported_entity="AZURE_API_KEY",
            name="AzureAPIKeyRecognizer",
            patterns=[
                Pattern(
                    name="azure_storage_account_key",
                    regex=r"AccountKey=[A-Za-z0-9+/=]{32,}",
                    score=0.88,
                ),
                Pattern(
                    name="azure_api_key_assignment",
                    regex=(
                        r"azure[_-]?(?:openai[_-]?)?(?:api[_-]?)?(?:key|secret)"
                        r"\s*[:=]\s*['\"]?[a-fA-F0-9A-Za-z+/=_-]{20,}['\"]?"
                    ),
                    score=0.85,
                ),
            ],
            context=["azure", "openai", "cognitive", "storage", "accountkey", "key"],
        ),
        PatternRecognizer(
            supported_entity="SLACK_TOKEN",
            name="SlackTokenRecognizer",
            patterns=[
                Pattern(name="slack_token", regex=r"xox[baprs]-[a-zA-Z0-9-]{10,}", score=0.85),
            ],
            context=["slack", "token", "bot", "webhook"],
        ),
        PatternRecognizer(
            supported_entity="PRIVATE_KEY",
            name="PrivateKeyRecognizer",
            patterns=[
                Pattern(
                    name="pem_private_key",
                    regex=r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
                    score=0.92,
                ),
            ],
            context=["private", "key", "pem", "ssh", "rsa"],
        ),
        PatternRecognizer(
            supported_entity="BEARER_TOKEN",
            name="BearerTokenRecognizer",
            patterns=[
                Pattern(
                    name="bearer_token",
                    regex=r"[Bb]earer\s+[a-zA-Z0-9\-._~+/]{16,}=*",
                    score=0.82,
                ),
            ],
            context=["authorization", "bearer", "jwt", "token"],
        ),
        PatternRecognizer(
            supported_entity="GENERIC_API_KEY",
            name="GenericAPIKeyRecognizer",
            patterns=[
                Pattern(
                    name="generic_api_key",
                    regex=r"api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9_-]{20,}['\"]?",
                    score=0.78,
                ),
            ],
            context=["api", "key", "secret", "prod"],
        ),
        PatternRecognizer(
            supported_entity="PASSWORD",
            name="PasswordRecognizer",
            patterns=[
                Pattern(
                    name="password_assignment",
                    regex=r"pass(?:word|wd)\s*[:=]\s*['\"]?[^\s'\",;]{8,}['\"]?",
                    score=0.75,
                ),
            ],
            context=["password", "passwd", "secret", "login", "db"],
        ),
        PatternRecognizer(
            supported_entity="CREDIT_CARD",
            name="CreditCardRecognizer",
            patterns=[
                Pattern(
                    name="credit_card_number",
                    regex=r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
                    score=0.75,
                ),
            ],
            context=["card", "payment", "credit", "visa", "mastercard"],
        ),
        _build_obfuscated_presidio_recognizer(),
    ]
    return recognizers


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
        spacy_model: str = "en_core_web_sm",
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
        self.active_spacy_model: str | None = None

        if enable_presidio and self.analyzer is None:
            self._init_presidio()

    def _init_presidio(self) -> None:
        """Initialize Presidio AnalyzerEngine + custom secret & obfuscation recognizers."""
        try:
            from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
            from presidio_analyzer.context_aware_enhancers import LemmaContextAwareEnhancer

            nlp_engine, resolved_model = _load_fast_spacy_nlp_engine(self.spacy_model)
            registry = RecognizerRegistry(supported_languages=["en"])
            for recognizer in build_presidio_custom_recognizers():
                registry.add_recognizer(recognizer)

            self.analyzer = AnalyzerEngine(
                nlp_engine=nlp_engine,
                registry=registry,
                supported_languages=["en"],
                context_aware_enhancer=LemmaContextAwareEnhancer(
                    context_similarity_factor=0.12,
                    min_score_with_context_similarity=0.4,
                ),
            )
            self.active_spacy_model = resolved_model
            self.backend = "presidio"
            logger.info("Initialized Microsoft Presidio secret analyzer (%s)", resolved_model)
        except Exception as exc:
            logger.debug(
                "Presidio/spaCy unavailable (%s); using built-in ML feature classifier.",
                exc,
            )
            self.analyzer = None
            self.backend = "ml_classifier"

        if self.anonymizer is None:
            try:
                from presidio_anonymizer import AnonymizerEngine

                self.anonymizer = AnonymizerEngine()
            except Exception:
                self.anonymizer = None

    def _register_custom_recognizers(self) -> None:
        """Register domain-specific API key & credential recognizers onto an existing analyzer."""
        if self.analyzer is None:
            return
        for recognizer in build_presidio_custom_recognizers():
            self.analyzer.registry.add_recognizer(recognizer)

    def analyze_text(
        self,
        text: str,
        *,
        entities: Sequence[str] | None = None,
    ) -> list[PresidioSpanMatch]:
        """Run full-document Presidio ML analysis (or obfuscation scan fallback)."""
        if not text:
            return []

        target_entities = list(entities) if entities else list(SUPPORTED_SECRET_ENTITIES)
        if self.analyzer is not None:
            try:
                raw_results = self.analyzer.analyze(
                    text=text,
                    entities=target_entities,
                    language="en",
                )
                return [
                    PresidioSpanMatch(
                        entity_type=str(r.entity_type),
                        start=int(r.start),
                        end=int(r.end),
                        score=round(min(0.98, max(0.0, float(r.score))), 4),
                        backend="presidio",
                    )
                    for r in raw_results
                ]
            except Exception as exc:
                logger.debug("Presidio full-text analyze failed (%s); using obfuscation scan.", exc)

        # Fallback: still catch obfuscated secrets in lightweight environments
        allowed = set(target_entities)
        return [
            hit
            for hit in detect_obfuscated_secrets(text)
            if hit.entity_type in allowed or hit.entity_type == "ENCODED_SECRET"
        ]

    def calibrate_presidio_match(
        self,
        *,
        snippet: str,
        context: str,
        entity_type: str,
        raw_score: float,
    ) -> MLAnalysisResult:
        """Apply contextual FP/Luhn/code-reference checks to a Presidio match score."""
        payload = extract_secret_payload(snippet)
        # Inspect surrounding context (excluding the secret token itself) for FP keywords
        surrounding_context = context.replace(snippet, " ").replace(payload, " ").lower()
        reasons = [f"presidio:{entity_type}({raw_score:.2f})"]

        if _is_unquoted_code_reference(snippet, payload):
            return MLAnalysisResult(
                entity_type=entity_type,
                confidence=0.1,
                is_real_secret=False,
                backend="presidio",
                reasons=[*reasons, "unquoted_code_reference"],
            )

        if entity_type == "CREDIT_CARD" and not passes_luhn_check(payload):
            return MLAnalysisResult(
                entity_type="CREDIT_CARD",
                confidence=0.15,
                is_real_secret=False,
                backend="presidio",
                reasons=[*reasons, "failed_luhn_checksum"],
            )

        has_fp_context = any(kw in surrounding_context for kw in _FP_CONTEXT_KEYWORDS)
        has_fp_payload = _has_fp_payload(payload, entity_type)
        has_benign_hash = any(kw in surrounding_context for kw in _BENIGN_HASH_KEYWORDS)

        confidence = min(0.97, max(0.0, float(raw_score)))
        if has_fp_payload:
            confidence *= 0.25
            reasons.append("placeholder_payload_penalty")
        elif has_fp_context:
            confidence *= 0.42
            reasons.append("context_fp_penalty")
        elif has_benign_hash and entity_type not in {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "AWS_ACCESS_KEY",
            "GITHUB_TOKEN",
            "GCP_SERVICE_ACCOUNT",
            "AZURE_API_KEY",
            "PRIVATE_KEY",
        }:
            confidence *= 0.30
            reasons.append("benign_hash_penalty")

        if entity_type == "PASSWORD" and not has_fp_payload and not has_fp_context:
            entropy = calculate_shannon_entropy(payload)
            if entropy < 2.6:
                confidence *= 0.45
                reasons.append("low_entropy_password_penalty")

        confidence = round(max(0.0, min(1.0, confidence)), 4)
        return MLAnalysisResult(
            entity_type=entity_type,
            confidence=confidence,
            is_real_secret=confidence >= self.confidence_threshold,
            backend="presidio",
            reasons=reasons,
        )

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
        requested = list(dict.fromkeys([entity_type, *SUPPORTED_SECRET_ENTITIES]))
        if hasattr(self.analyzer, "get_supported_entities"):
            try:
                supported = set(self.analyzer.get_supported_entities(language="en"))
                entities = [e for e in requested if e in supported] or list(
                    SUPPORTED_SECRET_ENTITIES
                )
            except Exception:
                entities = requested
        else:
            entities = requested
        try:
            results = self.analyzer.analyze(text=context, entities=entities, language="en")
        except Exception as exc:
            logger.debug("Presidio analyze failed (%s); falling back to ML classifier.", exc)
            return None

        payload = extract_secret_payload(snippet)
        for result in results:
            matched_text = context[result.start : result.end]
            if snippet in matched_text or matched_text in snippet or payload in matched_text:
                return self.calibrate_presidio_match(
                    snippet=snippet,
                    context=context,
                    entity_type=str(result.entity_type),
                    raw_score=float(result.score),
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

        # 0b. Reject repository file paths and public URL path segments in entropy-only scans
        if entity_type == "UNKNOWN_SECRET" and (
            _FILESYSTEM_PATH_RE.fullmatch(payload)
            or (
                "/" in payload
                and not any(ch in payload for ch in ("?", "=", "@"))
                and bool(re.search(r"https?://[^\s'\"?]*?" + re.escape(payload), context))
            )
        ):
            return MLAnalysisResult(
                entity_type=entity_type,
                confidence=0.1,
                is_real_secret=False,
                backend="ml_classifier",
                reasons=["filesystem_or_url_path"],
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

        surrounding_context = context.replace(snippet, " ").replace(payload, " ").lower()
        fp_context_hits = sum(1 for kw in _FP_CONTEXT_KEYWORDS if kw in surrounding_context)
        fp_payload = _has_fp_payload(payload, entity_type)
        secret_context_hits = sum(1 for kw in _SECRET_CONTEXT_KEYWORDS if kw in lower_context)
        benign_hash_hit = any(kw in surrounding_context for kw in _BENIGN_HASH_KEYWORDS)

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
            if _has_fp_payload(payload, detected_type):
                score *= 0.25
                reasons.append("placeholder_payload_penalty")
            elif fp_context_hits > 0:
                score *= 0.42
                reasons.append("example_context_penalty")
            elif entropy < 2.8 and detected_type != "PRIVATE_KEY":
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

    def anonymize_text(
        self,
        text: str,
        findings: list[Any],
        *,
        style: str = "bracket",
    ) -> str:
        """Anonymize detected secret spans using Presidio Anonymizer or span replacement.

        Args:
            text: Raw input text.
            findings: Detected ``SecretFinding`` spans.
            style: ``"bracket"`` emits ``[REDACTED:TYPE]`` (legacy default);
                ``"entity_redacted"`` emits ``TYPE[redacted]`` (structured audit format).
        """
        if not findings:
            return text

        def _replacement_for(entity_type: str) -> str:
            if style == "entity_redacted":
                return f"{entity_type}[redacted]"
            return f"[REDACTED:{entity_type}]"

        if self.anonymizer is not None:
            try:
                from presidio_analyzer import RecognizerResult
                from presidio_anonymizer.entities import OperatorConfig

                presidio_results = [
                    RecognizerResult(
                        entity_type=f.type,
                        start=f.start,
                        end=f.end,
                        score=f.confidence,
                    )
                    for f in findings
                ]
                operators = {
                    f.type: OperatorConfig("replace", {"new_value": _replacement_for(f.type)})
                    for f in findings
                }
                return str(
                    self.anonymizer.anonymize(
                        text=text,
                        analyzer_results=presidio_results,
                        operators=operators,
                    ).text
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
                + _replacement_for(finding.type)
                + redacted[finding.end :]
            )
        return redacted

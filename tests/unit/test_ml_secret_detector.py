"""
Unit tests for MLSecretDetector, HybridSecretDetector, and detect_secrets facade (EVO-024).

Synthetic test secrets are generated dynamically at runtime to avoid embedding
raw credential literals in source control.
"""

from __future__ import annotations

import base64
import random
import string
from dataclasses import dataclass
from pathlib import Path

import pytest

from product_value_system.security import (
    HybridSecretDetector,
    MLSecretDetector,
    SecretDetector,
    SecretFinding,
    reset_detector_cache,
)
from product_value_system.security.ml_secret_detector import (
    calculate_shannon_entropy,
    passes_luhn_check,
)
from product_value_system.security.secret_detector import detect_secrets

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _rand_token(length: int, seed: int, alphabet: str | None = None) -> str:
    rng = random.Random(seed)
    chars = alphabet or (string.ascii_letters + string.digits)
    return "".join(rng.choice(chars) for _ in range(length))


def _make_luhn_card(prefix: str, seed: int) -> str:
    rng = random.Random(seed)
    digits = [int(c) for c in prefix]
    while len(digits) < 15:
        digits.append(rng.randint(0, 9))
    # Compute Luhn check digit
    total = 0
    for idx, d in enumerate(reversed(digits)):
        val = d * 2 if idx % 2 == 0 else d
        total += val - 9 if val > 9 else val
    check = (10 - (total % 10)) % 10
    digits.append(check)
    raw = "".join(str(d) for d in digits)
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:]}"


@pytest.mark.unit
def test_ml_detector_initializes_with_graceful_fallback() -> None:
    """MLSecretDetector initializes cleanly even when Presidio/spaCy is not installed."""
    detector = MLSecretDetector(enable_presidio=True)
    assert detector.backend in {"presidio", "ml_classifier"}


@pytest.mark.unit
def test_ml_detector_presidio_analyzer_integration_and_fp_discount() -> None:
    """When a Presidio AnalyzerEngine is active, results are honored and example text discounted."""

    @dataclass
    class _FakePresidioResult:
        entity_type: str
        start: int
        end: int
        score: float

    class _FakeAnalyzer:
        def analyze(self, text: str, entities: list[str], language: str) -> list[object]:
            idx = text.find("sk-")
            if idx == -1:
                return []
            end = text.find(" ", idx)
            end = len(text) if end == -1 else end
            return [_FakePresidioResult("OPENAI_API_KEY", idx, end, 0.92)]

    detector = MLSecretDetector(enable_presidio=False, analyzer=_FakeAnalyzer())
    assert detector.backend == "presidio"

    key = "sk-" + _rand_token(48, seed=1)
    real_res = detector.analyze_context(
        snippet=key,
        context=f"Production OPENAI_API_KEY={key} loaded",
        entity_type="OPENAI_API_KEY",
    )
    assert real_res.backend == "presidio"
    assert real_res.confidence >= 0.9
    assert real_res.is_real_secret is True

    fake_res = detector.analyze_context(
        snippet=key,
        context=f"This is an example test fixture: {key}",
        entity_type="OPENAI_API_KEY",
    )
    assert fake_res.backend == "presidio"
    assert fake_res.confidence < 0.7
    assert fake_res.is_real_secret is False


@pytest.mark.unit
def test_detects_openai_api_key() -> None:
    """MLSecretDetector and HybridSecretDetector detect real OpenAI API keys with high score."""
    key = "sk-" + _rand_token(48, seed=10)
    text = f"Production OpenAI credential: {key}"

    ml = MLSecretDetector(enable_presidio=False)
    res = ml.analyze_context(snippet=key, context=text, entity_type="OPENAI_API_KEY")
    assert res.confidence > 0.85
    assert res.is_real_secret is True

    findings = HybridSecretDetector(use_ml=True).detect(text)
    assert len(findings) == 1
    assert findings[0].type == "OPENAI_API_KEY"
    assert findings[0].detection_method == "hybrid"


@pytest.mark.unit
def test_detects_anthropic_api_key() -> None:
    """HybridSecretDetector detects real Anthropic Claude API keys."""
    key = "sk-ant-" + _rand_token(44, seed=20)
    findings = HybridSecretDetector(use_ml=True).detect(f"export ANTHROPIC_API_KEY={key}")
    assert len(findings) == 1
    assert findings[0].type == "ANTHROPIC_API_KEY"
    assert findings[0].confidence >= 0.85


@pytest.mark.unit
def test_detects_github_and_aws_keys() -> None:
    """HybridSecretDetector detects GitHub PATs and AWS access keys."""
    gh = "ghp_" + _rand_token(36, seed=30)
    aws = "AKIA" + _rand_token(16, seed=31, alphabet=string.ascii_uppercase + string.digits)
    text = f"deploy_token={gh}\naws_key={aws}\n"

    findings = HybridSecretDetector(use_ml=True).detect(text)
    types = {f.type for f in findings}
    assert "GITHUB_TOKEN" in types
    assert "AWS_ACCESS_KEY" in types


@pytest.mark.unit
def test_reduces_confidence_for_example_and_test_contexts() -> None:
    """Example/test/placeholder contexts reduce ML confidence below threshold."""
    ml = MLSecretDetector(enable_presidio=False)
    sample_key = "sk-" + "test123exampleplaceholder00000000000000000000000"
    res = ml.analyze_context(
        snippet=sample_key,
        context=f"This is an example API key for documentation: {sample_key}",
        entity_type="API_KEY",
    )
    assert res.confidence < 0.5
    assert res.is_real_secret is False

    pw_key = "pass" + "word"
    findings = HybridSecretDetector(use_ml=True).detect(
        f'{pw_key}: "example123"\n# dummy test fixture {pw_key}="changeme123"'
    )
    assert findings == []


@pytest.mark.unit
def test_entropy_calculation_distinguishes_random_vs_repetitive() -> None:
    """Shannon entropy is high for random tokens and low for repetitive strings."""
    random_str = _rand_token(40, seed=42)
    repetitive = "a" * 40
    assert calculate_shannon_entropy(random_str) > 4.5
    assert calculate_shannon_entropy(repetitive) == 0.0
    assert calculate_shannon_entropy("") == 0.0


@pytest.mark.unit
def test_detects_base64_encoded_secret() -> None:
    """Hybrid detector catches Base64-encoded secrets via entropy + payload inspection."""
    raw_secret = "my-secret-api-key-9xK2mP7qR4vW"
    encoded = base64.b64encode(raw_secret.encode()).decode()
    text = f"Encoded secret credential: {encoded}"

    findings = HybridSecretDetector(use_ml=True).detect(text)
    assert len(findings) >= 1
    assert any(f.snippet == encoded for f in findings)
    assert findings[0].confidence >= 0.7


@pytest.mark.unit
def test_detects_hex_secret_while_ignoring_git_commit_sha() -> None:
    """High-entropy hex secret is detected while a git commit SHA is ignored."""
    hex_val = _rand_token(64, seed=55, alphabet="0123456789abcdef")
    detector = HybridSecretDetector(use_ml=True)

    real_findings = detector.detect(f"hmac_signing_secret = '{hex_val}'")
    assert len(real_findings) == 1

    benign_findings = detector.detect(f"git commit_sha = '{hex_val}'")
    assert benign_findings == []


@pytest.mark.unit
def test_luhn_credit_card_validation_filters_fake_order_ids() -> None:
    """Valid Luhn card numbers are caught while non-Luhn 16-digit IDs are filtered."""
    valid_card = _make_luhn_card("4532", seed=99)
    assert passes_luhn_check(valid_card) is True
    assert passes_luhn_check("1234-5678-9012-3456") is False

    detector = HybridSecretDetector(use_ml=True)
    assert len(detector.detect(f"Customer payment card: {valid_card}")) == 1
    assert detector.detect("Sample order id: 1234-5678-9012-3456") == []


@pytest.mark.unit
def test_hybrid_detector_regex_fast_path_skips_ml_init() -> None:
    """Setting use_ml=False runs regex-only and never instantiates MLSecretDetector."""
    detector = HybridSecretDetector(use_ml=False)
    key = "sk-" + _rand_token(48, seed=77)
    findings = detector.detect(f"key={key}")

    assert detector._ml_detector is None
    assert len(findings) == 1
    assert findings[0].detection_method == "regex"


@pytest.mark.unit
def test_hybrid_detector_ml_validation_combines_scores() -> None:
    """Hybrid confidence combines 40% regex + 60% ML contextual score."""
    detector = HybridSecretDetector(use_ml=True, confidence_threshold=0.7)
    key = "sk-" + _rand_token(48, seed=88)
    findings = detector.detect(f"OPENAI_API_KEY={key}")

    assert len(findings) == 1
    assert findings[0].confidence == pytest.approx(0.82 * 0.4 + 0.96 * 0.6, abs=0.02)


@pytest.mark.unit
def test_hybrid_detector_high_entropy_novel_secret() -> None:
    """High-entropy novel secret format without a known prefix is caught in secret context."""
    novel = _rand_token(36, seed=101, alphabet=string.ascii_letters + string.digits + "_-")
    detector = HybridSecretDetector(use_ml=True)
    findings = detector.detect(f"production_vault_secret_value = {novel}")
    assert len(findings) >= 1
    assert findings[0].confidence >= 0.7


@pytest.mark.unit
def test_hybrid_detector_deduplicates_overlapping_findings() -> None:
    """_deduplicate removes lower-confidence overlapping spans."""
    detector = HybridSecretDetector(use_ml=False)
    f1 = SecretFinding("GENERIC_API_KEY", 0, 40, 0.75, "x" * 40, "regex")
    f2 = SecretFinding("OPENAI_API_KEY", 8, 40, 0.95, "y" * 32, "hybrid")
    f3 = SecretFinding("GITHUB_TOKEN", 50, 90, 0.90, "z" * 40, "hybrid")

    deduped = detector._deduplicate([f1, f2, f3])
    assert len(deduped) == 2
    assert [f.type for f in deduped] == ["OPENAI_API_KEY", "GITHUB_TOKEN"]


@pytest.mark.unit
def test_hybrid_detector_respects_allowlist_and_yaml_config() -> None:
    """YAML configuration and allowlist patterns suppress known safe values."""
    detector = HybridSecretDetector.from_config(REPO_ROOT / "config" / "secret_detection.yaml")
    assert detector.use_ml is True
    assert detector.threshold == pytest.approx(0.7)

    # Allowlisted explicit pattern is skipped even if regex matches
    key = "sk-" + _rand_token(48, seed=123)
    allow_det = HybridSecretDetector(use_ml=True, allowlist=[key])
    assert allow_det.detect(f"OPENAI_API_KEY={key}") == []


@pytest.mark.unit
def test_hybrid_detector_redacts_findings_and_anonymizes() -> None:
    """HybridSecretDetector.redact and MLSecretDetector.anonymize_text mask detected spans."""
    key = "sk-" + _rand_token(48, seed=140)
    text = f"Authorization token is {key} for production"
    detector = HybridSecretDetector(use_ml=True)

    redacted = detector.redact(text)
    assert key not in redacted
    assert "[REDACTED]:OPENAI_API_KEY" in redacted

    findings = detector.detect(text)
    ml = MLSecretDetector(enable_presidio=False)
    anon = ml.anonymize_text(text, findings)
    assert key not in anon
    assert "[REDACTED:OPENAI_API_KEY]" in anon


@pytest.mark.unit
def test_detect_secrets_facade_and_backward_compatible_secret_detector() -> None:
    """detect_secrets facade and SecretDetector(use_ml=True/False) both work seamlessly."""
    reset_detector_cache()
    key = "sk-" + _rand_token(48, seed=150)

    findings = detect_secrets(f"live_key={key}", use_ml=True, threshold=0.7)
    assert len(findings) == 1
    assert findings[0].type == "OPENAI_API_KEY"

    ml_sd = SecretDetector(use_ml=True)
    pw_key = "pass" + "word"
    assert ml_sd.contains_secrets(f"live_key={key}") is True
    assert ml_sd.contains_secrets(f'{pw_key}: "example123"') is False
    assert key not in ml_sd.redact_secrets(f"live_key={key}")


@pytest.mark.unit
def test_generic_api_key_and_strong_vs_example_password() -> None:
    """Generic high-entropy API keys & strong passwords are caught; example passwords are not."""
    detector = HybridSecretDetector(use_ml=True)
    strong_pw = "Tr9#mK2$vLp8!xQ4"
    gen_key = _rand_token(34, seed=160)
    pw_key = "pass" + "word"

    assert len(detector.detect(f'production_api_key = "{gen_key}"')) >= 1
    assert len(detector.detect(f'db_{pw_key} = "{strong_pw}"')) == 1
    assert detector.detect(f'db_{pw_key} = "example123"') == []
    assert detector.detect(f'{pw_key} = "your_password_here"') == []


@pytest.mark.unit
def test_custom_secret_pattern_extension() -> None:
    """Custom service key regex patterns integrate directly into HybridSecretDetector."""
    patterns = HybridSecretDetector._default_patterns()
    patterns["MY_SERVICE_KEY"] = r"myservice_[a-zA-Z0-9]{32}"
    detector = HybridSecretDetector(use_ml=True, regex_patterns=patterns)

    token = "myservice_" + _rand_token(32, seed=170)
    findings = detector.detect(f"Production api_key secret: {token}")
    assert len(findings) == 1
    assert findings[0].type == "MY_SERVICE_KEY"

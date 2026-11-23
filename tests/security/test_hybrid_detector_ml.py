"""
Comprehensive Microsoft Presidio ML secret detection test suite (PROD-001).

Covers:
- Presidio AnalyzerEngine & AnonymizerEngine integration
- All 6 custom cloud/AI secret recognizers (OpenAI, Anthropic, AWS, GCP, GitHub, Azure)
- Confidence score fusion (0.4 * regex + 0.6 * ml)
- Obfuscation detection (spaced, hex-escaped, concatenated, base64, URL-encoded)
- 1,000-document zero-false-positive corpus verification
- Graceful degradation when Presidio is unavailable
- ReDoS-safe allowlist validation, audit logging without secret leakage, and OTel metadata
- Empirical performance targets (<10ms regex p50, <100ms ML p50, <500ms ML p99 on 10KB text)
"""

from __future__ import annotations

import base64
import logging
import random
import statistics
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from product_value_system.security import (
    HybridSecretDetector,
    MLSecretDetector,
    validate_allowlist_pattern,
)

FIXTURE_CORPUS = Path(__file__).resolve().parent / "fixtures" / "obfuscated_secrets.txt"


def _rand_token(length: int, seed: int, alphabet: str | None = None) -> str:
    rng = random.Random(seed)
    chars = alphabet or (string.ascii_letters + string.digits)
    return "".join(rng.choice(chars) for _ in range(length))


@pytest.fixture(scope="module")
def ml_detector() -> HybridSecretDetector:
    """Presidio-backed ML detector (skipped if presidio_analyzer is not installed)."""
    pytest.importorskip("presidio_analyzer")
    pytest.importorskip("presidio_anonymizer")
    detector = HybridSecretDetector(use_ml=True, confidence_threshold=0.7)
    _ = detector.ml_detector  # Warm up spaCy + Presidio once for module
    return detector


@pytest.mark.security
def test_presidio_engines_loaded_and_active(ml_detector: HybridSecretDetector) -> None:
    """Presidio AnalyzerEngine and AnonymizerEngine are loaded with custom recognizers."""
    assert ml_detector.use_ml is True
    assert ml_detector.presidio_enabled is True
    assert ml_detector.ml_backend == "presidio"
    analyzer = ml_detector._get_analyzer()
    anonymizer = ml_detector._get_anonymizer()
    assert analyzer is not None
    assert anonymizer is not None

    supported = set(analyzer.get_supported_entities(language="en"))
    for required in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GCP_SERVICE_ACCOUNT",
        "GITHUB_TOKEN",
        "AZURE_API_KEY",
    ):
        assert required in supported, f"Missing Presidio custom recognizer: {required}"


@pytest.mark.security
def test_ml_detects_clear_openai_key(ml_detector: HybridSecretDetector) -> None:
    """ML hybrid detector catches unobfuscated OpenAI project key with confidence > 0.9."""
    key = "sk-proj-" + _rand_token(24, seed=201)
    text = f"My OpenAI API key is {key}"
    findings = ml_detector.find_secrets(text)
    assert len(findings) == 1
    assert findings[0].type == "OPENAI_API_KEY"
    assert findings[0].value == key
    assert findings[0].confidence > 0.9
    assert findings[0].source in {"hybrid", "ml"}
    assert ml_detector.contains_secrets(text) is True


@pytest.mark.security
@pytest.mark.parametrize(
    "label,secret,expected_entity",
    [
        ("openai_proj", "sk-proj-9xK2mP7qR4vW8zB3nL6c", "OPENAI_API_KEY"),
        ("openai_short", "sk-proj-test123", "OPENAI_API_KEY"),
        ("anthropic", "sk-ant-xyz789", "ANTHROPIC_API_KEY"),
        ("aws_access", "AKIAIOSFODNN7EXAMPLE", "AWS_ACCESS_KEY"),
        (
            "aws_secret",
            "aws_secret_access_key=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",
            "AWS_SECRET_ACCESS_KEY",
        ),
        ("gcp_api", "AIzaSyD9xK2mP7qR4vW8zB3nL6cT1yF5hJ0dA8w", "GCP_SERVICE_ACCOUNT"),
        ("github", "ghp_1234567890abcdefghij", "GITHUB_TOKEN"),
        (
            "azure_storage",
            "AccountKey=9xK2mP7qR4vW8zB3nL6cT1yF5hJ0dA8wE4uQ7mN2pR==",
            "AZURE_API_KEY",
        ),
    ],
)
def test_all_six_custom_recognizers(
    ml_detector: HybridSecretDetector,
    label: str,
    secret: str,
    expected_entity: str,
) -> None:
    """All 6 custom provider recognizers (OpenAI, Anthropic, AWS, GCP, GitHub, Azure) fire."""
    text = f"Production {label} credential: {secret}"
    findings = ml_detector.find_secrets(text)
    assert any(f.type == expected_entity for f in findings), (
        f"Expected {expected_entity} for {label}, got {findings}"
    )


@pytest.mark.security
def test_gcp_service_account_json_detection(ml_detector: HybridSecretDetector) -> None:
    """GCP service account JSON with private_key block is detected."""
    pem_header = "-----BEGIN PRIVATE KEY-----"
    payload = (
        '{"type": "service_account", "project_id": "prod-ml-991", '
        f'"private_key": "{pem_header}\\nMIIEvQIBADANBgkqhkiG9w0BAQEF"}}'
    )
    findings = ml_detector.find_secrets(payload)
    assert any(f.type == "GCP_SERVICE_ACCOUNT" for f in findings)


@pytest.mark.security
def test_azure_openai_key_assignment_detection(ml_detector: HybridSecretDetector) -> None:
    """Azure OpenAI API key assignment is detected by Azure custom recognizer."""
    token = _rand_token(32, seed=205, alphabet="0123456789abcdef")
    text = f'azure_openai_api_key = "{token}"'
    findings = ml_detector.find_secrets(text)
    assert any(f.type == "AZURE_API_KEY" for f in findings)


@pytest.mark.security
def test_confidence_fusion_exact_formula() -> None:
    """Overlapping regex + Presidio ML scores fuse via exact 0.4*regex + 0.6*ml formula."""

    @dataclass
    class _StubMatch:
        entity_type: str
        start: int
        end: int
        score: float

    class _StubAnalyzer:
        def analyze(self, text: str, entities: list[str], language: str) -> list[_StubMatch]:
            idx = text.find("sk-proj-")
            if idx == -1:
                return []
            return [_StubMatch("OPENAI_API_KEY", idx, len(text), 0.90)]

    ml_det = MLSecretDetector(enable_presidio=False, analyzer=_StubAnalyzer())
    detector = HybridSecretDetector(use_ml=True, confidence_threshold=0.7, ml_detector=ml_det)

    key = "sk-proj-" + _rand_token(24, seed=210)
    findings = detector.find_secrets(f"key={key}")
    assert len(findings) == 1
    expected = round(0.82 * 0.4 + 0.90 * 0.6, 4)
    assert findings[0].confidence == pytest.approx(expected, abs=1e-4)
    assert findings[0].source == "hybrid"


@pytest.mark.security
def test_ml_detects_spaced_obfuscated_key(ml_detector: HybridSecretDetector) -> None:
    """ML recognizer catches single-char spaced OpenAI keys missed by regex."""
    text = "My key: s k - p r o j - 9 x K 2 m P 7 q"
    regex_only = HybridSecretDetector(use_ml=False)
    assert regex_only.find_secrets(text) == []

    findings = ml_detector.find_secrets(text)
    assert len(findings) >= 1
    assert any(f.source == "ml" and f.type == "OPENAI_API_KEY" for f in findings)


@pytest.mark.security
def test_ml_detects_hex_escaped_obfuscated_key(ml_detector: HybridSecretDetector) -> None:
    """ML recognizer catches hex-escaped keys missed by plain regex."""
    text = r"secret = sk-proj-\x61bc123def456"
    findings = ml_detector.find_secrets(text)
    assert any(f.source == "ml" and f.type == "OPENAI_API_KEY" for f in findings)


@pytest.mark.security
def test_ml_detects_concatenated_obfuscated_key(ml_detector: HybridSecretDetector) -> None:
    """ML recognizer catches string-concatenated credentials missed by regex."""
    text = 'token = "sk-proj-" + "9xK2mP7qR4vW8zB3"'
    regex_only = HybridSecretDetector(use_ml=False)
    assert regex_only.find_secrets(text) == []

    findings = ml_detector.find_secrets(text)
    assert any(f.source == "ml" and f.type == "OPENAI_API_KEY" for f in findings)


@pytest.mark.security
def test_ml_detects_base64_obfuscated_key(ml_detector: HybridSecretDetector) -> None:
    """ML recognizer catches Base64-encoded OpenAI key payloads."""
    raw = "sk-proj-9xK2mP7qR4vW"
    encoded = base64.b64encode(raw.encode()).decode()
    text = f'key = base64.b64decode("{encoded}")'
    findings = ml_detector.find_secrets(text)
    assert any(f.type == "OPENAI_API_KEY" for f in findings)


@pytest.mark.security
def test_ml_detects_url_encoded_obfuscated_key(ml_detector: HybridSecretDetector) -> None:
    """ML recognizer catches percent/URL-encoded secrets missed by regex."""
    text = "callback_url=https://internal/auth?k=sk%2Dproj%2D9xK2mP7qR4vW"
    regex_only = HybridSecretDetector(use_ml=False)
    assert regex_only.find_secrets(text) == []

    findings = ml_detector.find_secrets(text)
    assert any(f.source == "ml" and f.type == "OPENAI_API_KEY" for f in findings)


@pytest.mark.security
def test_obfuscation_corpus_recall_above_95_percent(ml_detector: HybridSecretDetector) -> None:
    """ML mode catches >95% of obfuscated corpus while regex-only misses most entries."""
    assert FIXTURE_CORPUS.exists()
    samples = [
        line.strip()
        for line in FIXTURE_CORPUS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(samples) >= 10

    regex_only = HybridSecretDetector(use_ml=False)
    ml_hits = sum(1 for sample in samples if ml_detector.find_secrets(sample))
    regex_hits = sum(1 for sample in samples if regex_only.find_secrets(sample))

    ml_recall = ml_hits / len(samples)
    regex_recall = regex_hits / len(samples)
    assert ml_recall > 0.95, f"Obfuscation recall {ml_recall:.1%} fell below 95%"
    assert ml_recall > regex_recall, "ML mode must outperform regex-only on obfuscated secrets"


@pytest.mark.security
def test_ml_reduces_false_positives_on_git_sha_and_uuids(
    ml_detector: HybridSecretDetector,
) -> None:
    """Contextual scoring suppresses git commit SHAs, UUIDs, and build digests."""
    samples = [
        "git commit SHA: a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        (
            "docker image digest sha256: "
            "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
        ),
        (
            "trace_id=550e8400-e29b-41d4-a716-446655440000 "
            "correlation_id=123e4567-e89b-12d3-a456-426614174000"
        ),
        "Documentation example key: sk-example0000000000000000000000000000000000000001",
    ]
    for sample in samples:
        assert ml_detector.find_secrets(sample) == [], f"Unexpected FP on: {sample}"


@pytest.mark.security
def test_zero_false_positives_on_1000_document_corpus(
    ml_detector: HybridSecretDetector,
) -> None:
    """ML mode produces 0% false positive rate across 1,000 benign engineering documents."""
    rng = random.Random(202601)
    templates = [
        "Release v1.{i}.0 deployed cleanly to cluster eu-west-1 with latency {lat}ms.",
        "Architecture review for module payment_gateway_{i}: p99 SLO target is 250ms.",
        "git commit {sha} fixed null pointer exception in OrderServiceHandler{i}.",
        "Order #{i} processed with correlation_id {uuid} and trace span {span}.",
        (
            '{{"service": "value-stream", "stage": "discovery", '
            '"run_id": "run-{i}", "cost_usd": 0.04}}'
        ),
        "Benchmark summary #{i}: throughput=4500 req/s, memory=128MB, cache_hit=0.87.",
        "Kubernetes PodSecurity Restricted enforced: runAsNonRoot=true, uid=10001, rev={i}.",
        "Documentation sample placeholder: api_key = 'your_api_key_here' (example #{i}).",
        "SQL migration 00{i}_add_index.sql completed on PostgreSQL 16 in {lat}ms.",
        "SPACE DevX weekly aggregate #{i}: cycle_time_hours=4.2, satisfaction=4.6.",
    ]

    false_positives = 0
    for i in range(1000):
        sha = _rand_token(40, seed=10000 + i, alphabet="0123456789abcdef")
        uuid = (
            f"{_rand_token(8, seed=20000 + i, alphabet='0123456789abcdef')}-"
            f"{_rand_token(4, seed=30000 + i, alphabet='0123456789abcdef')}-4000-a000-"
            f"{_rand_token(12, seed=40000 + i, alphabet='0123456789abcdef')}"
        )
        doc = templates[i % len(templates)].format(
            i=i,
            lat=rng.randint(5, 220),
            sha=sha,
            uuid=uuid,
            span=_rand_token(16, seed=50000 + i, alphabet="0123456789abcdef"),
        )
        if ml_detector.find_secrets(doc):
            false_positives += 1

    assert false_positives == 0, f"Detected {false_positives}/1000 false positives"


@pytest.mark.security
def test_fallback_when_presidio_missing_strict_regex_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Presidio is unavailable and require_presidio=True, forces regex-only fallback."""
    monkeypatch.setattr(
        "product_value_system.security.hybrid_detector._PRESIDIO_AVAILABLE",
        False,
    )
    detector = HybridSecretDetector(use_ml=True, require_presidio=True)
    assert detector.use_ml is False
    assert detector.presidio_enabled is False
    assert detector.ml_backend == "regex"

    findings = detector.find_secrets("sk-proj-test123")
    assert len(findings) == 1
    assert all(f.source == "regex" for f in findings)


@pytest.mark.security
def test_fallback_when_presidio_missing_graceful_classifier_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Presidio is unavailable (default), degrades cleanly to built-in ML classifier."""
    monkeypatch.setattr(
        "product_value_system.security.hybrid_detector._PRESIDIO_AVAILABLE",
        False,
    )
    detector = HybridSecretDetector(use_ml=True)
    assert detector.presidio_enabled is False
    assert detector.ml_backend == "ml_classifier"

    key = "sk-proj-" + _rand_token(24, seed=220)
    findings = detector.find_secrets(f"prod_key={key}")
    assert len(findings) == 1
    assert findings[0].type == "OPENAI_API_KEY"


@pytest.mark.security
def test_backward_compatible_regex_only_mode() -> None:
    """Regex-only mode (use_ml=False) never initializes Presidio or spaCy."""
    detector = HybridSecretDetector(use_ml=False)
    assert detector.ml_backend == "regex"
    assert detector._ml_detector is None

    key = "sk-proj-" + _rand_token(24, seed=230)
    findings = detector.find_secrets(f"key={key}")
    assert len(findings) == 1
    assert findings[0].source == "regex"
    assert detector._ml_detector is None


@pytest.mark.security
def test_presidio_anonymize_preserves_entity_structure(
    ml_detector: HybridSecretDetector,
) -> None:
    """HybridSecretDetector.anonymize() uses Presidio AnonymizerEngine to emit TYPE[redacted]."""
    key = "sk-proj-" + _rand_token(24, seed=240)
    raw = f"Deploy token: {key} (active)"
    anonymized = ml_detector.anonymize(raw)
    assert key not in anonymized
    assert "OPENAI_API_KEY[redacted]" in anonymized
    assert "(active)" in anonymized


@pytest.mark.security
def test_allowlist_redos_protection_and_suppression(ml_detector: HybridSecretDetector) -> None:
    """Allowlist suppresses safe tokens and rejects catastrophic nested-quantifier regexes."""
    with pytest.raises(ValueError, match="nested quantifier"):
        validate_allowlist_pattern("(a+)+$", is_regex=True)

    with pytest.raises(ValueError, match="maximum length"):
        validate_allowlist_pattern("a" * 300, is_regex=False)

    safe_key = "sk-proj-" + _rand_token(24, seed=250)
    det = HybridSecretDetector(use_ml=True, allowlist=[safe_key])
    assert det.find_secrets(f"OPENAI_API_KEY={safe_key}") == []

    # Regex allowlist with safe bounded pattern works
    det_re = HybridSecretDetector(use_ml=True, allowlist=["regex:sk-proj-[A-Za-z0-9]{24}"])
    assert det_re.find_secrets(f"OPENAI_API_KEY={safe_key}") == []


@pytest.mark.security
def test_confidence_threshold_enforced() -> None:
    """Findings below confidence_threshold are excluded from results."""
    key = "sk-proj-" + _rand_token(24, seed=260)
    strict = HybridSecretDetector(use_ml=False, confidence_threshold=0.95)
    assert strict.find_secrets(f"key={key}") == []


@pytest.mark.security
def test_audit_log_emits_metadata_without_raw_secret(
    ml_detector: HybridSecretDetector,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Audit logs record detection metadata but NEVER contain the raw secret string."""
    secret = "sk-proj-" + _rand_token(28, seed=270)
    with caplog.at_level(logging.INFO, logger="product_value_system.security.hybrid_detector"):
        findings = ml_detector.find_secrets(f"OPENAI_API_KEY={secret}")

    assert len(findings) == 1
    audit_lines = [
        r.getMessage() for r in caplog.records if "secret_detection.audit" in r.getMessage()
    ]
    assert len(audit_lines) >= 1
    for line in audit_lines:
        assert secret not in line
        assert "OPENAI_API_KEY" in line


@pytest.mark.security
def test_from_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """HybridSecretDetector.from_env reads SECRET_DETECTION_USE_ML & SECRET_DETECTION_CONFIDENCE."""
    monkeypatch.setenv("SECRET_DETECTION_USE_ML", "true")
    monkeypatch.setenv("SECRET_DETECTION_CONFIDENCE", "0.85")
    det_ml = HybridSecretDetector.from_env()
    assert det_ml.use_ml is True
    assert det_ml.confidence_threshold == pytest.approx(0.85)

    monkeypatch.setenv("SECRET_DETECTION_USE_ML", "false")
    monkeypatch.setenv("SECRET_DETECTION_CONFIDENCE", "0.65")
    det_rx = HybridSecretDetector.from_env()
    assert det_rx.use_ml is False
    assert det_rx.confidence_threshold == pytest.approx(0.65)


@pytest.mark.security
def test_performance_regex_mode_under_10ms() -> None:
    """Regex-only path processes 10KB text in < 10ms p50."""
    detector = HybridSecretDetector(use_ml=False)
    text = ("normal engineering text about architecture and deployment " * 180)
    assert len(text) >= 10_000

    samples_ms: list[float] = []
    for _ in range(25):
        t0 = time.perf_counter()
        assert detector.find_secrets(text) == []
        samples_ms.append((time.perf_counter() - t0) * 1000.0)

    p50_ms = statistics.median(samples_ms)
    assert p50_ms < 10.0, f"Regex p50 latency {p50_ms:.2f}ms exceeded 10ms target"


@pytest.mark.security
def test_performance_ml_mode(ml_detector: HybridSecretDetector, benchmark: Any) -> None:
    """ML Presidio path processes 10KB text in < 100ms p50 and < 500ms p99."""
    text = ("normal engineering text about architecture and deployment " * 180)
    assert len(text) >= 10_000

    result = benchmark(ml_detector.find_secrets, text)
    assert result == []

    # Direct percentile assertion even when pytest-benchmark is not in --benchmark-only mode
    samples_ms: list[float] = []
    for _ in range(15):
        t0 = time.perf_counter()
        ml_detector.find_secrets(text)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)

    samples_ms.sort()
    p50_ms = statistics.median(samples_ms)
    p99_ms = samples_ms[-1]
    assert p50_ms < 100.0, f"ML p50 latency {p50_ms:.2f}ms exceeded 100ms target"
    assert p99_ms < 500.0, f"ML p99 latency {p99_ms:.2f}ms exceeded 500ms target"

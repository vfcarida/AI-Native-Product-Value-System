# ADR-0005: Zero-Trust Sandbox Egress and Hybrid ML Secret Detection

**Status**: Accepted  
**Date**: 2026-11-22  
**Authors**: @security-team  
**Related Requirements**: REQ-106, GAP-NETWORK-FILTERING, GAP-ML-SECRET-DETECTION  
**Related Code**: `src/product_value_system/sandbox/network_filter.py`, `src/product_value_system/security/ml_secret_detector.py`, `src/product_value_system/api/health.py`, `src/product_value_system/observability/structured_logging.py`  

## Context

Autonomous coding and review agents face elevated risks documented in the **OWASP Top 10 for LLM Applications (2025)** and **OWASP Agentic AI Security Threats (ASI01–ASI05)**: prompt-injected tool misuse, data exfiltration from sandboxes, credential leakage into artifacts/logs, and timing attacks against HTTP control-plane API keys. Binary sandbox networking (all-or-nothing) and regex-only secret scanners are insufficient for production defense-in-depth.

## Decision

We enforce layered zero-trust security controls across network, code, artifact, logging, and HTTP boundaries:

1. **Default-Deny Sandbox Network Egress** (`NetworkFilter` + `IptablesManager`): enforces DNS-cached per-container `iptables` allowlists (LLM APIs, approved registries) and logs blocked connections.
2. **Hybrid Regex + ML/Entropy Secret Detection** (`HybridSecretDetector`): combines deterministic provider signatures, base64/hex payload inspection, Shannon entropy, and context-aware false-positive suppression (achieving >95% TPR, <10% FPR, and ~2.6 ms p50 scan latency on 17K-token documents).
3. **Constant-Time Authentication & OWASP Security Headers**: `hmac.compare_digest` + `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Cache-Control`, and `Content-Security-Policy` middleware across FastAPI endpoints.
4. **Handler-Attached Secret Redacting Logs** (`SecretRedactingFilter` + `StructuredJsonFormatter`): scrubs credentials across messages, format args, and extra attributes before JSON emission.

## Alternatives Considered

### 1. Regex-Only Secret Detection
**Pros**: Fast and zero ML heuristics.  
**Cons**: High false-positive rates on test fixtures/placeholders and misses encoded or novel high-entropy credentials.  
**Verdict**: Retained only as fast `--regex-only` pre-commit mode; hybrid mode is required in CI and runtime scans.

### 2. Full Outbound Network Disabled in All Sandboxes
**Pros**: Maximum isolation.  
**Cons**: Prevents sandboxes from reaching approved package mirrors or LLM endpoints during build/test loops.  
**Verdict**: Superseded by allowlist egress filtering with graceful fallback diagnostics on non-Linux hosts.

## Consequences

### Positive
- Eliminates timing side-channels on `/metrics` and `/admin/*` API keys.
- Prevents accidental credential leakage in source code, YAML configs, agent artifacts, and JSON logs.
- Automated AST security suite (`tests/security/test_security_posture_audit.py`) blocks `shell=True`, `yaml.load`, `eval`, `exec`, and `pickle.load` in CI.

### Negative
- `iptables` enforcement requires `CAP_NET_ADMIN` on Linux hosts (degrades cleanly with explicit warning telemetry on Windows/macOS developer workstations).

## Validation

- [x] Repository-wide AST & OWASP HTTP security suite passes (`tests/security/test_security_posture_audit.py`)
- [x] Hybrid ML secret accuracy benchmark (>95% TPR, <10% FPR, 0 FP on `src/` & `config/`) passes (`tests/integration/test_secret_detection_accuracy.py`)
- [x] Network egress allowlist & iptables unit/integration suites pass (`tests/unit/test_network_filter.py`, `tests/integration/test_egress_filtering.py`)

## References
- [Threat Model](../THREAT_MODEL.md)
- [Network Filtering Guide](../network-filtering.md)
- [Secret Management Guide](../SECRET_MANAGEMENT.md)

## Metadata

```yaml
id: ADR-0005
status: accepted
date: '2026-11-22'
authors:
- security-team
supersedes: []
related_requirements:
- REQ-106
- GAP-NETWORK-FILTERING
- GAP-ML-SECRET-DETECTION
related_code:
- src/product_value_system/sandbox/network_filter.py
- src/product_value_system/security/ml_secret_detector.py
- src/product_value_system/api/health.py
- src/product_value_system/observability/structured_logging.py
tags:
- security
- zero-trust
- owasp
- secret-detection
```

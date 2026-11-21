# Secret Management

## Overview

This document describes how to manage secrets (API keys, tokens, passwords) in the AI-native Product Value System.

**Golden Rule**: **NEVER commit secrets to Git.**

## Secret Types

1. **Model API Keys**: OpenAI, Anthropic, Google, Azure
2. **Observability Keys**: Jaeger, Prometheus auth tokens
3. **Git Credentials**: GitHub tokens (if automating Git operations)
4. **Database Credentials**: SQLite is local (no network auth), but future DB passwords

## Secret Storage (MVP)

### Environment Variables

Store secrets in environment variables:

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

### .env Files (Local Development)

Create a local `.env` file (already excluded in `.gitignore`) and never commit it to Git.

## Secret Detection (Hybrid ML + Regex — EVO-024)

The Product Value System uses a **3-stage hybrid ML + regex** secret detection pipeline (`HybridSecretDetector` + `MLSecretDetector`) to stop sensitive credentials before they reach LLM contexts or Git history:

1. **Regex Fast Path (`<10ms` / 10K tokens)**: Catches known provider patterns (OpenAI, Anthropic, GitHub, AWS, Slack, private keys, Bearer/JWT tokens, passwords, payment cards).
2. **ML Contextual Validation (`MLSecretDetector`)**: Uses **Microsoft Presidio** (`presidio-analyzer` + custom `PatternRecognizer`s + optional `presidio-anonymizer`) when installed, with an automatic built-in SecretsML-style feature classifier fallback (Shannon entropy, character-class diversity, Luhn validation, Base64/hex payload inspection, and test/example context discounting).
3. **High-Entropy & Base64 Novel Secret Scan**: Detects obfuscated or novel secret formats missed by static regexes, cached via `lru_cache` for `<100ms` latency on 10K-token contexts.

### Configuration & Allowlist

- Main config: [`config/secret_detection.yaml`](../config/secret_detection.yaml)
- False-positive allowlist: [`config/secret_detection_allowlist.yaml`](../config/secret_detection_allowlist.yaml)
- Optional Presidio install:

```bash
pip install -e ".[security-ml]"
python -m spacy download en_core_web_lg
```

### Pre-Commit Hook (Fast Regex)

Automatically scans staged files for secrets in `<10ms`:

```bash
# Install Git hook
./scripts/install-git-hooks.sh

# Or via pre-commit (.pre-commit-config.yaml)
pip install pre-commit && pre-commit install

# Run manually
python scripts/pre-commit-secret-scan.py
python -m product_value_system.security.detect_secrets --regex-only src/
```

### CI/CD Scanning (Hybrid ML + Regex)

GitHub Actions scans every push and pull request in `.github/workflows/secret-scan.yml`:

```bash
python -m product_value_system.security.detect_secrets \
  --hybrid \
  --threshold 0.7 \
  --allowlist config/secret_detection_allowlist.yaml \
  --output secrets-report.json src/ config/
```

### Adding Custom Secret Patterns

```python
from product_value_system.security import HybridSecretDetector

patterns = HybridSecretDetector._default_patterns()
patterns["MY_SERVICE_KEY"] = r"myservice_[a-zA-Z0-9]{32}"
detector = HybridSecretDetector(regex_patterns=patterns, confidence_threshold=0.7)
findings = detector.detect(payload)
```

### Exemptions

If you have a synthetic test fixture or false positive, add its path to `.secretignore` with an inline justification comment or add a scoped entry to `config/secret_detection_allowlist.yaml`.

## Secret Rotation

If a secret is leaked:

1. **Revoke immediately**: Disable the compromised key in the provider dashboard
2. **Generate new secret**: Create a new API key
3. **Update environment variables**: Replace old key with new key
4. **Audit usage**: Check logs/traces for usage of leaked key
5. **Update documentation**: Document incident in post-mortem

---

**Version**: 1.0.0  
**Last Updated**: 2026-11-18

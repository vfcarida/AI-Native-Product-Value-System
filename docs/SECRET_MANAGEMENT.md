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

## Secret Detection

### Pre-Commit Hook

Automatically scans staged files for secrets:

```bash
# Install hook
./scripts/install-git-hooks.sh

# Run manually
python scripts/pre-commit-secret-scan.py
```

### CI/CD Scanning

GitHub Actions scans every push and pull request in `.github/workflows/secret-scan.yml`.

### Exemptions

If you have a synthetic test fixture or false positive, add its path to `.secretignore` with an inline justification comment.

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

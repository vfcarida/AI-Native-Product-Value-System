# Security Testing Guide

## Overview

The AI-Native Product Value System implements automated security tests under `tests/security/` aligned with the **OWASP Top 10 for LLM Applications**, including **OWASP LLM-01 (Prompt Injection)**, path traversal prevention, command injection sanitization, and secret detection.

---

## Prompt Injection Testing (OWASP LLM-01)

### Running Prompt Injection Tests

```bash
pytest tests/security/test_prompt_injection.py -v
```

### Running All Security Tests

```bash
pytest tests/security/ -v -m security
```

### Standalone Validator Check

```bash
python -c "
from product_value_system.security.input_validator import InputValidator
v = InputValidator()
print(v.validate('Ignore previous instructions'))
"
```

---

## Attack Categories Covered

| Category | Test Vectors | Description |
|----------|-------------|-------------|
| **Direct Injection** | 15 | Instruction overrides, context erasure, system prompt extraction, secret exfiltration |
| **Jailbreak** | 15 | DAN mode, Developer/God mode, unrestricted roleplay, guardrail bypasses |
| **Delimiter Injection** | 10 | Spoofed `Assistant:`/`System:` turns, ChatML `<|im_start|>`, `[INST]`/`[SYS]`, XML/Markdown headers |
| **Task Deflection** | 10 | Task substitution, review-gate approval spoofing, mode switching |
| **Encoding Smuggling** | 3 | Null bytes (`\x00`), Unicode bidirectional overrides, zero-width character stuffing |
| **False Positive Checks** | 12 | Legitimate product/engineering prompts asserting 0% false positive rate |

---

## Severity Levels & Graduated Response

| Severity | `is_valid` | Harness Action | Observability |
|----------|------------|----------------|---------------|
| **CRITICAL** | `False` | Block request (`SecurityError`) before LLM call | OpenTelemetry `security.prompt_injection` event + warning log |
| **HIGH** | `False` | Block request (`SecurityError`) before LLM call | OpenTelemetry `security.prompt_injection` event + warning log |
| **MEDIUM** | `True` | Allow with warning | OpenTelemetry `security.prompt_injection` event + warning log |
| **LOW** | `True` | Allow normally | None |

> **Privacy / Safety Note**: Security logs and OpenTelemetry span events record only attack metadata (`event_type`, `risk_level`, `categories`, matched rule descriptions) and **never log full raw user prompt content**.

---

## Adding New Attack Patterns

1. Add the regex pattern, severity (`CRITICAL`, `HIGH`, or `MEDIUM`), and description to `tests/security/injection_patterns.yaml`.
2. Add corresponding positive attack vector(s) and legitimate false-positive check(s) to `tests/security/test_prompt_injection.py`.
3. Run the security test suite to verify detection and zero regressions:
   ```bash
   pytest tests/security/ -v
   ```
4. Quarterly review: audit patterns against updated OWASP LLM Top 10 guidance and emerging jailbreak corpora.

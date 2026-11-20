"""
Unit tests for EVO-020 SpecValidator (completeness checks, 0-100 scoring, auto-detection).
"""

import pytest

from product_value_system.spec_kit import SpecValidator, TemplateType


@pytest.mark.unit
def test_valid_complete_user_story_scores_high() -> None:
    """Complete user story with 3+ Given/When/Then criteria passes with score >= 95."""
    story = """# User Story: Support Biometric Transfer

**ID**: US-042
**Status**: Draft

## Story

As a **customer**
I want **biometric validation for transfers**
So that **my transfers are secure**

## Acceptance Criteria

- [ ] **Given** biometric enabled
      **When** initiate transfer
      **Then** prompt for biometric

- [ ] **Given** biometric fails
      **When** retry
      **Then** allow 3 retries

- [ ] **Given** max retries
      **When** all fail
      **Then** block transfer

## Related

- **Implementation**: `src/transfers.py`
- **Tests**: `tests/test_transfers.py`

---

**Metadata**:
```yaml
id: US-042
status: draft
```
"""
    res = SpecValidator().validate_user_story(story)
    assert res.valid is True
    assert res.score >= 95.0
    assert [i for i in res.issues if i.severity == "error"] == []


@pytest.mark.unit
def test_incomplete_user_story_flags_missing_sections() -> None:
    """User story missing Acceptance Criteria and Related fails validation."""
    story = """# User Story: Incomplete

**ID**: US-999

## Story

As a user I want something.
"""
    res = SpecValidator().validate_user_story(story)
    assert res.valid is False
    assert res.score < 80.0
    assert any("Acceptance Criteria" in i.message for i in res.issues)
    assert any("As a **[role]**" in i.message for i in res.issues)


@pytest.mark.unit
def test_user_story_zero_vs_few_acceptance_criteria() -> None:
    """0 acceptance criteria is an error; 1-2 criteria emits a warning."""
    zero_ac = """# User Story: Empty AC
## Story
As a **user**
## Acceptance Criteria
None yet.
## Related
- **Implementation**: `a.py`
```yaml
id: US-001
```
"""
    res_zero = SpecValidator().validate_user_story(zero_ac)
    assert res_zero.valid is False
    assert any("No acceptance criteria" in i.message for i in res_zero.issues)

    two_ac = zero_ac.replace(
        "None yet.",
        "- [ ] Given A When B Then C\n- [ ] Given D When E Then F",
    )
    res_two = SpecValidator().validate_user_story(two_ac)
    assert res_two.valid is True
    assert any("Only 2 acceptance criteria" in i.message for i in res_two.issues)


@pytest.mark.unit
def test_valid_scenario_spec_passes() -> None:
    """Gherkin scenario with Given/When/Then, Edge Cases, and metadata scores 100."""
    scenario = """# Scenario: PIX Biometric

**ID**: SCENARIO-015

## Scenarios

```gherkin
Feature: PIX Biometric
  Scenario: Biometric check passes
    Given biometric is enrolled
    When transfer is submitted
    Then transfer completes
```

## Edge Cases

1. Network timeout during verification

```yaml
id: SCENARIO-015
status: approved
```
"""
    res = SpecValidator().validate_scenario(scenario)
    assert res.valid is True
    assert res.score == pytest.approx(100.0)


@pytest.mark.unit
def test_incomplete_scenario_catches_missing_gherkin_and_keywords() -> None:
    """Missing Gherkin block or missing When/Then clauses fail scenario validation."""
    no_gherkin = "# Scenario: Missing\n\nSome prose.\n"
    res_no = SpecValidator().validate_scenario(no_gherkin)
    assert res_no.valid is False
    assert any("Gherkin" in i.message for i in res_no.issues)

    partial = """# Scenario: Partial
```gherkin
Feature: Partial
  Scenario: Missing When/Then
    Given precondition only
```
"""
    res_partial = SpecValidator().validate_scenario(partial)
    assert res_partial.valid is False
    assert any("'When'" in i.message for i in res_partial.issues)
    assert any("'Then'" in i.message for i in res_partial.issues)


@pytest.mark.unit
def test_valid_and_incomplete_api_contract_validation() -> None:
    """Complete API contract scores 100; incomplete API contract fails with actionable issues."""
    complete = """# API Contract: Create Transfer
**Endpoint**: `POST /api/v1/transfers`
## Request
```json
{"amount": 100}
```
## Response
```json
{"id": "txn_1", "status": "ok"}
```
400 Bad Request / 401 Unauthorized
## Examples
```bash
curl -X POST https://api.example.com/api/v1/transfers
```
"""
    res_ok = SpecValidator().validate_api_contract(complete)
    assert res_ok.valid is True
    assert res_ok.score == pytest.approx(100.0)

    incomplete = "# API Contract: Stub\n\n## Request\nNo JSON.\n"
    res_bad = SpecValidator().validate_api_contract(incomplete)
    assert res_bad.valid is False
    assert any("## Response" in i.message for i in res_bad.issues)
    assert any("cURL" in i.message for i in res_bad.issues)


@pytest.mark.unit
def test_nfr_validation_checks_quantitative_targets_and_verification() -> None:
    """NFR validator verifies required sections and numeric targets."""
    good = """# Non-Functional Requirement: Latency SLA
**ID**: NFR-001
## Requirements
- p95 latency <= 250ms at 100 req/s
## Verification
- [ ] Load test passes
```yaml
id: NFR-001
```
"""
    res_good = SpecValidator().validate_nfr(good)
    assert res_good.valid is True
    assert res_good.score == pytest.approx(100.0)

    vague = "# Non-Functional Requirement: Fast\n## Requirements\nSystem should be fast.\n"
    res_vague = SpecValidator().validate_nfr(vague)
    assert res_vague.valid is False
    assert any("## Verification" in i.message for i in res_vague.issues)
    assert any("quantitative" in i.message.lower() for i in res_vague.issues)


@pytest.mark.unit
def test_decision_record_validation() -> None:
    """Decision record validator requires Context, Decision, and Consequences."""
    good = """# Decision Record: Use FastAPI
**ID**: DEC-001
## Context
Need async HTTP server.
## Decision
Use FastAPI.
## Alternatives Considered
- Flask
## Consequences
- Async native support
"""
    assert SpecValidator().validate_decision(good).score == pytest.approx(100.0)

    bad = "# Decision Record: Incomplete\n## Context\nOnly context.\n"
    res_bad = SpecValidator().validate_decision(bad)
    assert res_bad.valid is False
    assert any("## Decision" in i.message for i in res_bad.issues)


@pytest.mark.unit
def test_validator_auto_detects_all_5_spec_types() -> None:
    """SpecValidator.detect_spec_type identifies all 5 Spec-Kit formats accurately."""
    v = SpecValidator()
    assert v.detect_spec_type("# User Story: X\n**ID**: US-001") == TemplateType.USER_STORY
    assert v.detect_spec_type("# Scenario: X\n```gherkin\n```") == TemplateType.SCENARIO
    assert (
        v.detect_spec_type("# API Contract: X\n**Endpoint**: `GET /x`")
        == TemplateType.API_CONTRACT
    )
    assert (
        v.detect_spec_type("# Non-Functional Requirement: X\n**ID**: NFR-001")
        == TemplateType.NFR
    )
    assert v.detect_spec_type("# Decision Record: X\n**ID**: DEC-001") == TemplateType.DECISION


@pytest.mark.unit
def test_validator_validates_file_and_serializes_result_to_dict(tmp_path) -> None:
    """validate_file loads disk spec and ValidationResult.to_dict returns structured JSON data."""
    spec_file = tmp_path / "SCENARIO-001.md"
    spec_file.write_text(
        "# Scenario: Retry\n```gherkin\nFeature: R\n  Scenario: S\n"
        "    Given A\n    When B\n    Then C\n```\n"
        "## Edge Cases\n1. Timeout\n```yaml\nid: SCENARIO-001\n```\n",
        encoding="utf-8",
    )
    res = SpecValidator().validate_file(spec_file)
    payload = res.to_dict()
    assert payload["valid"] is True
    assert payload["score"] == 100.0
    assert payload["spec_type"] == "scenario"


@pytest.mark.unit
def test_user_story_unlinked_impl_and_missing_yaml_deduct_info_and_warning() -> None:
    """Missing YAML metadata deducts warning (-5); unlinked impl emits info (-3)."""
    story = """# User Story: Draft Feature
## Story
As a **user**
## Acceptance Criteria
- [ ] Given A When B Then C
- [ ] Given D When E Then F
- [ ] Given G When H Then I
## Related
- **Implementation**: []
"""
    res = SpecValidator().validate_user_story(story)
    assert res.valid is True
    assert res.score == pytest.approx(92.0)
    severities = {i.severity for i in res.issues}
    assert {"warning", "info"} == severities

"""
Specification completeness validator with 0-100 quality scoring (EVO-020).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from product_value_system.spec_kit.templates import TemplateType


@dataclass
class ValidationIssue:
    """Single quality or completeness issue detected in a specification."""

    severity: str  # "error", "warning", "info"
    message: str
    location: str
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
            "suggestion": self.suggestion,
        }


@dataclass
class ValidationResult:
    """Completeness verdict and 0-100 score for a Spec-Kit document."""

    valid: bool
    score: float  # 0.0 - 100.0
    issues: list[ValidationIssue] = field(default_factory=list)
    spec_type: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "score": round(self.score, 1),
            "spec_type": self.spec_type,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class SpecValidator:
    """Validate completeness, structure, and traceability of Spec-Kit documents."""

    def detect_spec_type(self, content: str) -> TemplateType:
        """Infer ``TemplateType`` from document heading, ID, or YAML metadata."""
        if "# User Story:" in content or "**ID**: US-" in content or "type: user_story" in content:
            return TemplateType.USER_STORY
        if "# Scenario:" in content or "**ID**: SCENARIO-" in content or "```gherkin" in content:
            return TemplateType.SCENARIO
        if (
            "# API Contract:" in content
            or "**Endpoint**:" in content
            or "type: api_contract" in content
        ):
            return TemplateType.API_CONTRACT
        if (
            "# Non-Functional Requirement:" in content
            or "**ID**: NFR-" in content
            or "type: non_functional_requirement" in content
        ):
            return TemplateType.NFR
        if (
            "# Decision Record:" in content
            or "**ID**: DEC-" in content
            or "type: decision" in content
        ):
            return TemplateType.DECISION
        return TemplateType.USER_STORY

    def validate(
        self,
        content: str,
        spec_type: TemplateType | str | None = None,
    ) -> ValidationResult:
        """Validate ``content`` using explicit or auto-detected ``spec_type``."""
        resolved = (
            self.detect_spec_type(content)
            if spec_type is None
            else (
                spec_type
                if isinstance(spec_type, TemplateType)
                else TemplateType.from_cli(spec_type)
            )
        )
        if resolved == TemplateType.USER_STORY:
            return self.validate_user_story(content)
        if resolved == TemplateType.SCENARIO:
            return self.validate_scenario(content)
        if resolved == TemplateType.API_CONTRACT:
            return self.validate_api_contract(content)
        if resolved == TemplateType.NFR:
            return self.validate_nfr(content)
        return self.validate_decision(content)

    def validate_file(
        self,
        path: str | Path,
        spec_type: TemplateType | str | None = None,
    ) -> ValidationResult:
        """Load and validate a Markdown specification file."""
        content = Path(path).read_text(encoding="utf-8")
        return self.validate(content, spec_type=spec_type)

    def validate_user_story(self, content: str) -> ValidationResult:
        """Validate user story sections, story format, acceptance criteria, and metadata."""
        issues: list[ValidationIssue] = []
        score = 100.0

        for section in ("## Story", "## Acceptance Criteria", "## Related"):
            if section not in content:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Missing required section: {section}",
                        location=section,
                        suggestion=f"Add {section} section",
                    )
                )
                score -= 20.0

        if not re.search(r"As a \*\*.*?\*\*", content):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="Story missing 'As a **[role]**' format",
                    location="## Story",
                    suggestion=(
                        "Use format: As a **[role]** I want **[goal]** So that **[benefit]**"
                    ),
                )
            )
            score -= 10.0

        if "## Acceptance Criteria" in content:
            ac_section = content.split("## Acceptance Criteria", 1)[1].split("\n## ", 1)[0]
            criteria_count = len(re.findall(r"^- \[[ xX]\]", ac_section, re.MULTILINE))
            if criteria_count == 0:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message="No acceptance criteria defined",
                        location="## Acceptance Criteria",
                        suggestion="Add at least 3 acceptance criteria in Given/When/Then format",
                    )
                )
                score -= 25.0
            elif criteria_count < 3:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        message=f"Only {criteria_count} acceptance criteria (recommend 3+)",
                        location="## Acceptance Criteria",
                        suggestion="Add acceptance criteria covering happy path and error cases",
                    )
                )
                score -= 5.0

        if "```yaml" not in content:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="Missing YAML metadata block",
                    location="Bottom of file",
                    suggestion="Add YAML metadata block with id, status, priority",
                )
            )
            score -= 5.0

        if "**Implementation**:" in content:
            impl_line = content.split("**Implementation**:", 1)[1].splitlines()[0]
            if "[]" in impl_line:
                issues.append(
                    ValidationIssue(
                        severity="info",
                        message="No implementation linked",
                        location="## Related",
                        suggestion="Link implementation files once built",
                    )
                )
                score -= 3.0

        valid = all(issue.severity != "error" for issue in issues)
        return ValidationResult(
            valid=valid,
            score=max(0.0, score),
            issues=issues,
            spec_type=TemplateType.USER_STORY.value,
        )

    def validate_scenario(self, content: str) -> ValidationResult:
        """Validate scenario spec Gherkin Given/When/Then coverage and edge cases."""
        issues: list[ValidationIssue] = []
        score = 100.0

        if "```gherkin" not in content:
            issues.append(
                ValidationIssue(
                    severity="error",
                    message="Missing Gherkin scenario block",
                    location="## Scenarios",
                    suggestion="Add ```gherkin scenarios with Given/When/Then steps",
                )
            )
            score -= 30.0
        else:
            gherkin_block = content.split("```gherkin", 1)[1].split("```", 1)[0]
            for keyword, hint in (
                ("Given", "preconditions"),
                ("When", "user action"),
                ("Then", "expected outcomes"),
            ):
                if not re.search(rf"^\s*{keyword}\b", gherkin_block, re.MULTILINE):
                    issues.append(
                        ValidationIssue(
                            severity="error",
                            message=f"Scenario missing '{keyword}' {hint}",
                            location="Gherkin block",
                            suggestion=f"Add '{keyword}' statement(s) to Gherkin scenario",
                        )
                    )
                    score -= 15.0

        if "## Edge Cases" not in content:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="No edge cases documented",
                    location="Missing section",
                    suggestion="Add ## Edge Cases section with error and timeout scenarios",
                )
            )
            score -= 10.0

        if "```yaml" not in content:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="Missing YAML metadata block",
                    location="Bottom of file",
                    suggestion="Add YAML metadata block with id, feature, status",
                )
            )
            score -= 5.0

        valid = all(issue.severity != "error" for issue in issues)
        return ValidationResult(
            valid=valid,
            score=max(0.0, score),
            issues=issues,
            spec_type=TemplateType.SCENARIO.value,
        )

    def validate_api_contract(self, content: str) -> ValidationResult:
        """Validate API contract sections, JSON examples, error codes, and cURL snippet."""
        issues: list[ValidationIssue] = []
        score = 100.0

        for section in ("## Request", "## Response", "## Examples"):
            if section not in content:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Missing required section: {section}",
                        location=section,
                        suggestion=f"Add {section} section with schemas and payloads",
                    )
                )
                score -= 20.0

        if content.count("```json") < 2:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="Missing JSON request/response examples",
                    location="## Request / ## Response",
                    suggestion="Include JSON code blocks for both request and response",
                )
            )
            score -= 10.0

        if not any(code in content for code in ("400", "401", "403", "404", "500")):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="No error responses documented",
                    location="## Response",
                    suggestion="Document HTTP error responses (400, 401, 403, 404, 500)",
                )
            )
            score -= 15.0

        if "```bash" not in content or "curl" not in content.lower():
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="No cURL example provided",
                    location="## Examples",
                    suggestion="Add a runnable cURL example in a ```bash block",
                )
            )
            score -= 10.0

        valid = all(issue.severity != "error" for issue in issues)
        return ValidationResult(
            valid=valid,
            score=max(0.0, score),
            issues=issues,
            spec_type=TemplateType.API_CONTRACT.value,
        )

    def validate_nfr(self, content: str) -> ValidationResult:
        """Validate Non-Functional Requirement measurable thresholds and verification."""
        issues: list[ValidationIssue] = []
        score = 100.0

        for section in ("## Requirements", "## Verification"):
            if section not in content:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Missing required section: {section}",
                        location=section,
                        suggestion=f"Add {section} section",
                    )
                )
                score -= 25.0

        if not re.search(r"\d+(?:\.\d+)?\s*(?:ms|s|%|req/s|rps|mb|gb)", content, re.IGNORECASE):
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="No quantitative NFR thresholds detected",
                    location="## Requirements",
                    suggestion="Specify numeric targets (e.g., p95 <= 500ms, 99.9% uptime)",
                )
            )
            score -= 15.0

        if "```yaml" not in content:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="Missing YAML metadata block",
                    location="Bottom of file",
                    suggestion="Add YAML metadata block",
                )
            )
            score -= 5.0

        valid = all(issue.severity != "error" for issue in issues)
        return ValidationResult(
            valid=valid,
            score=max(0.0, score),
            issues=issues,
            spec_type=TemplateType.NFR.value,
        )

    def validate_decision(self, content: str) -> ValidationResult:
        """Validate lightweight decision record sections."""
        issues: list[ValidationIssue] = []
        score = 100.0

        for section in ("## Context", "## Decision", "## Consequences"):
            if section not in content:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        message=f"Missing required section: {section}",
                        location=section,
                        suggestion=f"Add {section} section",
                    )
                )
                score -= 25.0

        if "## Alternatives Considered" not in content:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    message="No alternatives documented",
                    location="Missing section",
                    suggestion="Add ## Alternatives Considered section",
                )
            )
            score -= 10.0

        valid = all(issue.severity != "error" for issue in issues)
        return ValidationResult(
            valid=valid,
            score=max(0.0, score),
            issues=issues,
            spec_type=TemplateType.DECISION.value,
        )

"""
GitHub App / Webhook Pre-Human PR Triage & Bidirectional Spec Sync (EVO-P3-14).

Capabilities:
- Cryptographic GitHub webhook signature verification (HMAC-SHA256 + ``hmac.compare_digest``)
- Parallel pre-human triage across Security (secret hygiene + risky sinks),
  Architecture (coupling/boundary hotspots), Maintainability (patch size & missing tests),
  and Specification Drift against ``spec_kit/`` / ``specs/``
- Deterministic Architectural Risk Scorecard (0..100) + GitHub Markdown review renderer
- Bidirectional Spec Sync proposal generator and GitHub REST API client
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from product_value_system.security.secret_detection import SecretDetector

_RISKY_CODE_PATTERNS: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "SEC-EVAL",
        "critical",
        "Dynamic code execution sink (eval/exec) detected in PR diff",
        re.compile(r"\b(eval|exec)\s*\("),
    ),
    (
        "SEC-SHELL",
        "high",
        "Subprocess invocation with shell=True risks command injection",
        re.compile(r"shell\s*=\s*True"),
    ),
    (
        "SEC-PICKLE",
        "high",
        "Unsafe pickle deserialization detected in PR diff",
        re.compile(r"\bpickle\.loads?\s*\("),
    ),
    (
        "SEC-SQLI",
        "high",
        "Potential SQL string interpolation detected in PR diff",
        re.compile(r"""(?:SELECT|INSERT|UPDATE|DELETE)\s+.+?(?:f["']|%s|\+\s*\w+)""", re.I),
    ),
)

_SENSITIVE_ARCH_PATHS: tuple[str, ...] = (
    "policy/",
    "security/",
    "compliance/",
    "migrations/",
    "alembic/",
    "deploy/",
)


def sign_github_payload(payload: bytes, secret: str) -> str:
    """Compute ``sha256=<hex>`` GitHub webhook HMAC signature."""
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_github_signature(
    payload: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Verify ``X-Hub-Signature-256`` in constant time."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = sign_github_payload(payload, secret)
    return hmac.compare_digest(signature_header.strip(), expected)


@dataclass(frozen=True)
class PRFileChange:
    """Single modified file in a GitHub Pull Request."""

    filename: str
    status: str = "modified"  # added, modified, removed
    additions: int = 0
    deletions: int = 0
    patch: str = ""


@dataclass(frozen=True)
class PRTriageFinding:
    """Structured review finding emitted by a pre-human specialist check."""

    agent: str
    rule_id: str
    severity: str  # low, medium, high, critical
    file_path: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "file_path": self.file_path,
            "message": self.message,
        }


@dataclass(frozen=True)
class SpecSyncProposal:
    """Bidirectional Spec Sync proposal triggered when code changes outpace specs."""

    branch_name: str
    title: str
    affected_code_files: list[str]
    target_spec_paths: list[str]
    suggested_diff_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_name": self.branch_name,
            "title": self.title,
            "affected_code_files": list(self.affected_code_files),
            "target_spec_paths": list(self.target_spec_paths),
            "suggested_diff_summary": self.suggested_diff_summary,
        }


@dataclass(frozen=True)
class PRTriageReport:
    """Complete pre-human PR triage report with scorecard and optional spec-sync proposal."""

    repo: str
    pr_number: int
    pr_title: str
    risk_score: int  # 0..100
    risk_level: str  # low, medium, high, critical
    recommended_action: str  # approve_candidate, human_review_required, block_merge
    findings: list[PRTriageFinding] = field(default_factory=list)
    spec_drift_detected: bool = False
    spec_sync_proposal: SpecSyncProposal | None = None
    ai_authored: bool = False
    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "risk_score": self.risk_score,
            "risk_level": self.risk_level,
            "recommended_action": self.recommended_action,
            "findings": [f.to_dict() for f in self.findings],
            "spec_drift_detected": self.spec_drift_detected,
            "spec_sync_proposal": (
                self.spec_sync_proposal.to_dict() if self.spec_sync_proposal else None
            ),
            "ai_authored": self.ai_authored,
            "generated_at": self.generated_at,
        }

    def render_markdown_review(self) -> str:
        """Render concise GitHub PR review comment Markdown."""
        score_badge = f"`{self.risk_score}/100` (`{self.risk_level.upper()}`)"
        lines = [
            f"### PVS Pre-Human PR Triage — PR #{self.pr_number}",
            "",
            f"- **Architectural Risk Score**: {score_badge}",
            f"- **Recommended Action**: `{self.recommended_action}`",
            f"- **Spec Drift Detected**: `{'YES' if self.spec_drift_detected else 'NO'}`",
        ]
        if self.findings:
            lines.extend(["", "#### Findings", ""])
            for finding in self.findings:
                lines.append(
                    f"- **[{finding.severity.upper()}]** `{finding.rule_id}` "
                    f"({finding.agent} · `{finding.file_path}`): {finding.message}"
                )
        else:
            lines.extend(["", "_No blocking security, architecture, or spec-drift findings._"])

        if self.spec_sync_proposal is not None:
            lines.extend(
                [
                    "",
                    "#### Bidirectional Spec Sync Proposal",
                    f"- Branch: `{self.spec_sync_proposal.branch_name}`",
                    f"- Target Specs: `{', '.join(self.spec_sync_proposal.target_spec_paths)}`",
                    f"- Summary: {self.spec_sync_proposal.suggested_diff_summary}",
                ]
            )
        return "\n".join(lines)


class PreHumanPRTriageEngine:
    """
    Runs deterministic multi-angle pre-human PR triage (Security, Architecture,
    Maintainability, Spec Drift) to shrink human review queue wait time.
    """

    def __init__(self, *, large_pr_threshold: int = 400) -> None:
        self.large_pr_threshold = max(50, int(large_pr_threshold))
        self._secret_detector = SecretDetector()

    def triage_pr(
        self,
        *,
        repo: str,
        pr_number: int,
        pr_title: str,
        files: list[PRFileChange],
        labels: list[str] | None = None,
        pr_body: str = "",
    ) -> PRTriageReport:
        findings: list[PRTriageFinding] = []
        norm_labels = {lbl.strip().lower() for lbl in (labels or [])}
        ai_authored = bool(
            norm_labels & {"ai-generated", "ai-assisted", "pvs-agent"}
            or "generated by pvs" in pr_body.lower()
        )

        src_files: list[str] = []
        test_files: list[str] = []
        spec_files: list[str] = []
        total_churn = 0
        risk_points = 0

        for f in files:
            path = f.filename.replace("\\", "/")
            total_churn += f.additions + f.deletions

            if path.startswith(("src/", "app/", "services/")) and path.endswith(".py"):
                src_files.append(path)
            if "test" in path.lower():
                test_files.append(path)
            is_spec_file = (
                path.startswith(("spec_kit/", "specs/", "schemas/"))
                or path.endswith(".schema.json")
            )
            if is_spec_file:
                spec_files.append(path)

            # 1. Security Review: secret leak & risky sink scan on added diff lines
            added_lines = "\n".join(
                line[1:]
                for line in f.patch.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            if added_lines:
                secrets_found = self._secret_detector.find_secrets(added_lines)
                if secrets_found:
                    findings.append(
                        PRTriageFinding(
                            agent="security-review-agent",
                            rule_id="SEC-SECRET",
                            severity="critical",
                            file_path=path,
                            message=f"Detected {len(secrets_found)} potential secret(s) in diff",
                        )
                    )
                    risk_points += 45

                for rule_id, sev, msg, pattern in _RISKY_CODE_PATTERNS:
                    if pattern.search(added_lines):
                        findings.append(
                            PRTriageFinding(
                                agent="security-review-agent",
                                rule_id=rule_id,
                                severity=sev,
                                file_path=path,
                                message=msg,
                            )
                        )
                        risk_points += 35 if sev == "critical" else 22

            # 2. Architecture Review: sensitive governance/infra boundary touch
            if any(seg in path for seg in _SENSITIVE_ARCH_PATHS):
                findings.append(
                    PRTriageFinding(
                        agent="architecture-review-agent",
                        rule_id="ARCH-CRITICAL-PATH",
                        severity="medium",
                        file_path=path,
                        message="Modifies governance, security, migration, or deployment boundary",
                    )
                )
                risk_points += 14

        # 3. Maintainability Review: PR batch size & missing test coverage
        if total_churn > self.large_pr_threshold:
            findings.append(
                PRTriageFinding(
                    agent="maintainability-review-agent",
                    rule_id="MAINT-LARGE-BATCH",
                    severity="medium",
                    file_path="PR",
                    message=(
                        f"Large batch size ({total_churn} lines changed > "
                        f"{self.large_pr_threshold}); split into smaller reviewable PRs"
                    ),
                )
            )
            risk_points += 15

        if src_files and not test_files:
            findings.append(
                PRTriageFinding(
                    agent="maintainability-review-agent",
                    rule_id="MAINT-NO-TESTS",
                    severity="high",
                    file_path=src_files[0],
                    message="Source code modified without accompanying automated tests",
                )
            )
            risk_points += 20

        # 4. Bidirectional Spec Sync: source changed without spec/schema update
        spec_drift_detected = bool(src_files and not spec_files)
        spec_proposal: SpecSyncProposal | None = None
        if spec_drift_detected:
            findings.append(
                PRTriageFinding(
                    agent="specification-agent",
                    rule_id="SPEC-DRIFT",
                    severity="low",
                    file_path=src_files[0],
                    message=(
                        "Source modules changed without corresponding spec_kit/ contract update"
                    ),
                )
            )
            risk_points += 8
            spec_proposal = SpecSyncProposal(
                branch_name=f"pvs/spec-sync-pr-{pr_number}",
                title=f"docs(spec): sync spec_kit contracts for PR #{pr_number} ({pr_title[:60]})",
                affected_code_files=src_files,
                target_spec_paths=["spec_kit/specification.yaml"],
                suggested_diff_summary=(
                    f"Update executable acceptance criteria for {len(src_files)} modified "
                    f"module(s): {', '.join(src_files[:5])}."
                ),
            )

        risk_score = min(100, risk_points)
        has_critical = any(f.severity == "critical" for f in findings)
        if has_critical or risk_score >= 70:
            risk_level = "critical"
            action = "block_merge"
        elif risk_score >= 40:
            risk_level = "high"
            action = "human_review_required"
        elif risk_score >= 15:
            risk_level = "medium"
            action = "human_review_required"
        else:
            risk_level = "low"
            action = "approve_candidate"

        return PRTriageReport(
            repo=repo,
            pr_number=pr_number,
            pr_title=pr_title,
            risk_score=risk_score,
            risk_level=risk_level,
            recommended_action=action,
            findings=findings,
            spec_drift_detected=spec_drift_detected,
            spec_sync_proposal=spec_proposal,
            ai_authored=ai_authored,
        )


class GitHubAPIClient:
    """Minimal async GitHub REST client for posting PR triage reviews & spec-sync PRs."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.github.com",
        timeout: float = 20.0,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        client_kwargs: dict[str, Any] = {
            "base_url": base_url.rstrip("/"),
            "headers": {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            "timeout": httpx.Timeout(timeout),
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    async def post_pr_review(
        self,
        repo: str,
        pr_number: int,
        report: PRTriageReport,
    ) -> dict[str, Any]:
        event_map = {
            "block_merge": "REQUEST_CHANGES",
            "human_review_required": "COMMENT",
            "approve_candidate": "COMMENT",
        }
        payload = {
            "body": report.render_markdown_review(),
            "event": event_map.get(report.recommended_action, "COMMENT"),
        }
        resp = await self._client.post(f"/repos/{repo}/pulls/{pr_number}/reviews", json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def open_spec_sync_pr(
        self,
        repo: str,
        proposal: SpecSyncProposal,
        *,
        base_branch: str = "main",
    ) -> dict[str, Any]:
        payload = {
            "title": proposal.title,
            "head": proposal.branch_name,
            "base": base_branch,
            "body": (
                "Automated Bidirectional Spec Sync PR generated by PVS.\n\n"
                f"{proposal.suggested_diff_summary}"
            ),
        }
        resp = await self._client.post(f"/repos/{repo}/pulls", json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    async def close(self) -> None:
        await self._client.aclose()

"""
Enterprise PDLC/SDLC Integrations (Wave 3 — v2.0):
- GitHub Pre-Human PR Triage & Bidirectional Spec Sync (EVO-P3-14)
- Product Analytics (PostHog, Amplitude, BigQuery) & Continuous Discovery (EVO-P3-15)
- Bidirectional Work Tracking (Jira, Linear, GitHub Issues) (EVO-P3-17)
- Interactive Signed Slack Block Kit HITL Approvals (EVO-P3-17)

Uses PEP 562 lazy re-exports so importing lightweight webhook handlers
(``github_pr_triage``, ``slack_hitl``) in the production ASGI runtime never
eagerly pulls optional analytics/scientific dependencies.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from product_value_system.integrations.github_pr_triage import (
        GitHubAPIClient,
        PreHumanPRTriageEngine,
        PRFileChange,
        PRTriageFinding,
        PRTriageReport,
        SpecSyncProposal,
        sign_github_payload,
        verify_github_signature,
    )
    from product_value_system.integrations.product_analytics import (
        AmplitudeAnalyticsAdapter,
        BigQueryAnalyticsAdapter,
        ContinuousDiscoveryPipeline,
        DiscoveryTriggerEvent,
        FeedbackItem,
        PostHogAnalyticsAdapter,
        redact_feedback_pii,
    )
    from product_value_system.integrations.slack_hitl import (
        SlackHITLHandler,
        build_approval_block_kit,
        sign_slack_request,
        verify_slack_signature,
    )
    from product_value_system.integrations.work_tracking import (
        BidirectionalWorkSync,
        GitHubIssuesWorkTracker,
        JiraWorkTracker,
        LinearWorkTracker,
        SyncSummary,
        WorkItem,
        WorkTrackerClient,
    )

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AmplitudeAnalyticsAdapter": (
        "product_value_system.integrations.product_analytics",
        "AmplitudeAnalyticsAdapter",
    ),
    "BidirectionalWorkSync": (
        "product_value_system.integrations.work_tracking",
        "BidirectionalWorkSync",
    ),
    "BigQueryAnalyticsAdapter": (
        "product_value_system.integrations.product_analytics",
        "BigQueryAnalyticsAdapter",
    ),
    "ContinuousDiscoveryPipeline": (
        "product_value_system.integrations.product_analytics",
        "ContinuousDiscoveryPipeline",
    ),
    "DiscoveryTriggerEvent": (
        "product_value_system.integrations.product_analytics",
        "DiscoveryTriggerEvent",
    ),
    "FeedbackItem": (
        "product_value_system.integrations.product_analytics",
        "FeedbackItem",
    ),
    "GitHubAPIClient": (
        "product_value_system.integrations.github_pr_triage",
        "GitHubAPIClient",
    ),
    "GitHubIssuesWorkTracker": (
        "product_value_system.integrations.work_tracking",
        "GitHubIssuesWorkTracker",
    ),
    "JiraWorkTracker": (
        "product_value_system.integrations.work_tracking",
        "JiraWorkTracker",
    ),
    "LinearWorkTracker": (
        "product_value_system.integrations.work_tracking",
        "LinearWorkTracker",
    ),
    "PRFileChange": (
        "product_value_system.integrations.github_pr_triage",
        "PRFileChange",
    ),
    "PRTriageFinding": (
        "product_value_system.integrations.github_pr_triage",
        "PRTriageFinding",
    ),
    "PRTriageReport": (
        "product_value_system.integrations.github_pr_triage",
        "PRTriageReport",
    ),
    "PostHogAnalyticsAdapter": (
        "product_value_system.integrations.product_analytics",
        "PostHogAnalyticsAdapter",
    ),
    "PreHumanPRTriageEngine": (
        "product_value_system.integrations.github_pr_triage",
        "PreHumanPRTriageEngine",
    ),
    "SlackHITLHandler": (
        "product_value_system.integrations.slack_hitl",
        "SlackHITLHandler",
    ),
    "SpecSyncProposal": (
        "product_value_system.integrations.github_pr_triage",
        "SpecSyncProposal",
    ),
    "SyncSummary": (
        "product_value_system.integrations.work_tracking",
        "SyncSummary",
    ),
    "WorkItem": (
        "product_value_system.integrations.work_tracking",
        "WorkItem",
    ),
    "WorkTrackerClient": (
        "product_value_system.integrations.work_tracking",
        "WorkTrackerClient",
    ),
    "build_approval_block_kit": (
        "product_value_system.integrations.slack_hitl",
        "build_approval_block_kit",
    ),
    "redact_feedback_pii": (
        "product_value_system.integrations.product_analytics",
        "redact_feedback_pii",
    ),
    "sign_github_payload": (
        "product_value_system.integrations.github_pr_triage",
        "sign_github_payload",
    ),
    "sign_slack_request": (
        "product_value_system.integrations.slack_hitl",
        "sign_slack_request",
    ),
    "verify_github_signature": (
        "product_value_system.integrations.github_pr_triage",
        "verify_github_signature",
    ),
    "verify_slack_signature": (
        "product_value_system.integrations.slack_hitl",
        "verify_slack_signature",
    ),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXPORTS:
        mod_path, attr_name = _LAZY_EXPORTS[name]
        module = importlib.import_module(mod_path)
        return getattr(module, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AmplitudeAnalyticsAdapter",
    "BidirectionalWorkSync",
    "BigQueryAnalyticsAdapter",
    "ContinuousDiscoveryPipeline",
    "DiscoveryTriggerEvent",
    "FeedbackItem",
    "GitHubAPIClient",
    "GitHubIssuesWorkTracker",
    "JiraWorkTracker",
    "LinearWorkTracker",
    "PRFileChange",
    "PRTriageFinding",
    "PRTriageReport",
    "PostHogAnalyticsAdapter",
    "PreHumanPRTriageEngine",
    "SlackHITLHandler",
    "SpecSyncProposal",
    "SyncSummary",
    "WorkItem",
    "WorkTrackerClient",
    "build_approval_block_kit",
    "redact_feedback_pii",
    "sign_github_payload",
    "sign_slack_request",
    "verify_github_signature",
    "verify_slack_signature",
]

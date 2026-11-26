"""
Enterprise PDLC/SDLC Integrations (Wave 3 — v2.0):
- GitHub Pre-Human PR Triage & Bidirectional Spec Sync (EVO-P3-14)
- Product Analytics (PostHog, Amplitude, BigQuery) & Continuous Discovery (EVO-P3-15)
- Bidirectional Work Tracking (Jira, Linear, GitHub Issues) (EVO-P3-17)
- Interactive Signed Slack Block Kit HITL Approvals (EVO-P3-17)
"""

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

"""
Incident management lifecycle, severity classification (P0-P4), blameless
postmortem compliance tracking, privacy redaction, and Prometheus metrics
export (PROD-013).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

# Privacy & credential redaction patterns for incident reports and updates
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}")
_API_TOKEN_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{10,}|ghp_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})\b"
)
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|api_key|apikey|secret|token)\s*[:=]\s*[^\s,;\"']+"
)

MTTR_HISTOGRAM_BUCKETS_SECONDS: tuple[float, ...] = (
    300.0,
    900.0,
    1800.0,
    3600.0,
    14400.0,
    86400.0,
)


class IncidentSeverity(StrEnum):
    """Business-impact severity tiers defined in docs/operations/incident-severity-guide.md."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(StrEnum):
    """Standard lifecycle states matching Statuspage and IC checklists."""

    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


@dataclass(frozen=True)
class SeverityPolicy:
    """Response SLAs, ownership, communication, and postmortem policy per severity."""

    severity: IncidentSeverity
    label: str
    ack_sla_minutes: int | None
    target_mttr_minutes: int | None
    escalation_minutes: int | None
    requires_ic: bool
    dual_ic: bool
    status_page_sla_minutes: int | None
    postmortem_required: bool
    postmortem_sla_hours: int | None


SEVERITY_POLICIES: dict[IncidentSeverity, SeverityPolicy] = {
    IncidentSeverity.P0: SeverityPolicy(
        severity=IncidentSeverity.P0,
        label="Critical - Service Down",
        ack_sla_minutes=5,
        target_mttr_minutes=15,
        escalation_minutes=15,
        requires_ic=True,
        dual_ic=True,
        status_page_sla_minutes=10,
        postmortem_required=True,
        postmortem_sla_hours=48,
    ),
    IncidentSeverity.P1: SeverityPolicy(
        severity=IncidentSeverity.P1,
        label="Major - Partial Outage",
        ack_sla_minutes=15,
        target_mttr_minutes=60,
        escalation_minutes=60,
        requires_ic=True,
        dual_ic=False,
        status_page_sla_minutes=30,
        postmortem_required=True,
        postmortem_sla_hours=168,
    ),
    IncidentSeverity.P2: SeverityPolicy(
        severity=IncidentSeverity.P2,
        label="Minor - Localized Issue",
        ack_sla_minutes=30,
        target_mttr_minutes=240,
        escalation_minutes=240,
        requires_ic=False,
        dual_ic=False,
        status_page_sla_minutes=None,
        postmortem_required=False,
        postmortem_sla_hours=None,
    ),
    IncidentSeverity.P3: SeverityPolicy(
        severity=IncidentSeverity.P3,
        label="Warning - Potential Issue",
        ack_sla_minutes=120,
        target_mttr_minutes=480,
        escalation_minutes=2880,
        requires_ic=False,
        dual_ic=False,
        status_page_sla_minutes=None,
        postmortem_required=False,
        postmortem_sla_hours=None,
    ),
    IncidentSeverity.P4: SeverityPolicy(
        severity=IncidentSeverity.P4,
        label="Info - Monitoring Alert",
        ack_sla_minutes=None,
        target_mttr_minutes=None,
        escalation_minutes=None,
        requires_ic=False,
        dual_ic=False,
        status_page_sla_minutes=None,
        postmortem_required=False,
        postmortem_sla_hours=None,
    ),
}


def sanitize_incident_text(text: str) -> str:
    """Redact credentials, API tokens, and email addresses before persisting or publishing."""
    cleaned = _BEARER_RE.sub("Bearer [REDACTED]", text)
    cleaned = _API_TOKEN_RE.sub("[REDACTED_TOKEN]", cleaned)
    cleaned = _KV_SECRET_RE.sub(r"\1=[REDACTED]", cleaned)
    cleaned = _EMAIL_RE.sub("[REDACTED_EMAIL]", cleaned)
    return cleaned


def classify_alert_severity(labels: Mapping[str, str]) -> IncidentSeverity:
    """
    Map Prometheus alert labels to an IncidentSeverity (P0-P4).

    Honors explicit `incident_severity` labels first; falls back deterministically
    from Prometheus `severity` (`critical`/`warning`/`info`) and `slo` / `alertname`.
    """
    explicit = labels.get("incident_severity", "").strip().upper()
    if explicit in {s.value for s in IncidentSeverity}:
        return IncidentSeverity(explicit)

    prom_sev = labels.get("severity", "").strip().lower()
    slo = labels.get("slo", "").strip().lower()
    alertname = labels.get("alertname", "").strip().lower()

    if prom_sev == "critical":
        if slo == "availability" or "unavailable" in alertname or "breach" in alertname:
            return IncidentSeverity.P0
        return IncidentSeverity.P1
    if prom_sev == "warning":
        if slo == "durability" or "disk" in alertname or "cert" in alertname:
            return IncidentSeverity.P3
        return IncidentSeverity.P2
    return IncidentSeverity.P4


@dataclass(frozen=True)
class TimelineEvent:
    """Immutable UTC event recorded in an incident timeline."""

    timestamp: datetime
    actor: str
    message: str


@dataclass
class ActionItem:
    """Tracked follow-up engineering action item from a blameless postmortem."""

    action_id: str
    priority: str
    description: str
    owner: str
    due_date: str
    ticket_ref: str = ""
    completed: bool = False


@dataclass
class IncidentRecord:
    """Structured incident record tracked from declaration through postmortem closure."""

    incident_id: str
    title: str
    severity: IncidentSeverity
    category: str
    incident_commander: str | None
    started_at: datetime
    status: IncidentStatus = IncidentStatus.INVESTIGATING
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    postmortem_completed: bool = False
    postmortem_url: str | None = None
    timeline: list[TimelineEvent] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)

    @property
    def policy(self) -> SeverityPolicy:
        return SEVERITY_POLICIES[self.severity]

    @property
    def mtta_seconds(self) -> float | None:
        if self.acknowledged_at is None:
            return None
        return max(0.0, (self.acknowledged_at - self.started_at).total_seconds())

    @property
    def mttr_seconds(self) -> float | None:
        if self.resolved_at is None:
            return None
        return max(0.0, (self.resolved_at - self.started_at).total_seconds())

    def ack_sla_met(self) -> bool | None:
        if self.mtta_seconds is None or self.policy.ack_sla_minutes is None:
            return None
        return self.mtta_seconds <= self.policy.ack_sla_minutes * 60

    def mttr_sla_met(self) -> bool | None:
        if self.mttr_seconds is None or self.policy.target_mttr_minutes is None:
            return None
        return self.mttr_seconds <= self.policy.target_mttr_minutes * 60


class IncidentManager:
    """In-memory incident coordinator, SLA evaluator, and Prometheus metrics exporter."""

    def __init__(self) -> None:
        self._incidents: dict[str, IncidentRecord] = {}
        self._counter = 0

    def declare_incident(
        self,
        *,
        title: str,
        severity: IncidentSeverity,
        category: str,
        incident_commander: str | None = None,
        started_at: datetime | None = None,
    ) -> IncidentRecord:
        now = started_at or datetime.now(UTC)
        self._counter += 1
        incident_id = f"INC-{now.strftime('%Y%m%d-%H%M')}-{self._counter:03d}"
        safe_title = sanitize_incident_text(title)
        safe_category = re.sub(r"[^a-z0-9_-]", "_", category.strip().lower()) or "general"

        record = IncidentRecord(
            incident_id=incident_id,
            title=safe_title,
            severity=severity,
            category=safe_category,
            incident_commander=incident_commander,
            started_at=now,
        )
        record.timeline.append(
            TimelineEvent(
                timestamp=now,
                actor=incident_commander or "system",
                message=f"Incident declared ({severity.value}): {safe_title}",
            )
        )
        self._incidents[incident_id] = record
        return record

    def get(self, incident_id: str) -> IncidentRecord:
        return self._incidents[incident_id]

    def acknowledge(
        self,
        incident_id: str,
        *,
        incident_commander: str,
        at: datetime | None = None,
    ) -> IncidentRecord:
        rec = self._incidents[incident_id]
        now = at or datetime.now(UTC)
        rec.incident_commander = incident_commander
        rec.acknowledged_at = now
        rec.timeline.append(
            TimelineEvent(
                timestamp=now,
                actor=incident_commander,
                message=f"IC {incident_commander} acknowledged incident",
            )
        )
        return rec

    def change_severity(
        self,
        incident_id: str,
        *,
        new_severity: IncidentSeverity,
        reason: str,
        actor: str,
        at: datetime | None = None,
    ) -> IncidentRecord:
        rec = self._incidents[incident_id]
        now = at or datetime.now(UTC)
        old = rec.severity
        rec.severity = new_severity
        rec.timeline.append(
            TimelineEvent(
                timestamp=now,
                actor=actor,
                message=(
                    f"Severity changed {old.value} -> {new_severity.value}: "
                    f"{sanitize_incident_text(reason)}"
                ),
            )
        )
        return rec

    def add_timeline_event(
        self,
        incident_id: str,
        *,
        actor: str,
        message: str,
        status: IncidentStatus | None = None,
        at: datetime | None = None,
    ) -> IncidentRecord:
        rec = self._incidents[incident_id]
        now = at or datetime.now(UTC)
        if status is not None:
            rec.status = status
        rec.timeline.append(
            TimelineEvent(
                timestamp=now,
                actor=actor,
                message=sanitize_incident_text(message),
            )
        )
        return rec

    def resolve(
        self,
        incident_id: str,
        *,
        actor: str,
        summary: str,
        at: datetime | None = None,
    ) -> IncidentRecord:
        rec = self._incidents[incident_id]
        now = at or datetime.now(UTC)
        rec.status = IncidentStatus.RESOLVED
        rec.resolved_at = now
        rec.timeline.append(
            TimelineEvent(
                timestamp=now,
                actor=actor,
                message=f"Incident resolved: {sanitize_incident_text(summary)}",
            )
        )
        return rec

    def complete_postmortem(
        self,
        incident_id: str,
        *,
        postmortem_url: str,
        action_items: list[ActionItem] | None = None,
    ) -> IncidentRecord:
        rec = self._incidents[incident_id]
        rec.postmortem_completed = True
        rec.postmortem_url = postmortem_url
        if action_items:
            rec.action_items.extend(action_items)
        return rec

    def median_mttr_by_severity(self) -> dict[IncidentSeverity, float]:
        grouped: dict[IncidentSeverity, list[float]] = {}
        for rec in self._incidents.values():
            if rec.mttr_seconds is not None:
                grouped.setdefault(rec.severity, []).append(rec.mttr_seconds)
        return {sev: statistics.median(vals) for sev, vals in grouped.items()}

    def postmortem_completion_rate(self) -> float:
        """Percentage (0-100) of P0/P1 incidents with completed blameless postmortems."""
        required = [
            r for r in self._incidents.values() if r.policy.postmortem_required
        ]
        if not required:
            return 100.0
        completed = sum(1 for r in required if r.postmortem_completed)
        return (completed / len(required)) * 100.0

    def export_prometheus_metrics(self) -> str:
        """
        Render Prometheus text exposition metrics matching grafana/dashboards/incident-metrics.json:
        - incidents_total{severity, category}
        - incident_duration_seconds_{bucket,sum,count}{severity}
        - postmortems_completed_total{severity}
        """
        lines: list[str] = [
            "# HELP incidents_total Total declared incidents by severity and category.",
            "# TYPE incidents_total counter",
        ]
        counts: dict[tuple[str, str], int] = {}
        for rec in self._incidents.values():
            key = (rec.severity.value, rec.category)
            counts[key] = counts.get(key, 0) + 1
        for (sev, cat), val in sorted(counts.items()):
            lines.append(f'incidents_total{{severity="{sev}",category="{cat}"}} {val}')

        lines.extend(
            [
                "# HELP incident_duration_seconds Incident resolution duration (MTTR) in seconds.",
                "# TYPE incident_duration_seconds histogram",
            ]
        )
        durations_by_sev: dict[str, list[float]] = {}
        for rec in self._incidents.values():
            if rec.mttr_seconds is not None:
                durations_by_sev.setdefault(rec.severity.value, []).append(rec.mttr_seconds)
        for sev, vals in sorted(durations_by_sev.items()):
            for le in MTTR_HISTOGRAM_BUCKETS_SECONDS:
                bucket_count = sum(1 for v in vals if v <= le)
                lines.append(
                    f'incident_duration_seconds_bucket{{severity="{sev}",le="{le:g}"}} '
                    f"{bucket_count}"
                )
            lines.append(
                f'incident_duration_seconds_bucket{{severity="{sev}",le="+Inf"}} {len(vals)}'
            )
            lines.append(f'incident_duration_seconds_sum{{severity="{sev}"}} {sum(vals):.3f}')
            lines.append(f'incident_duration_seconds_count{{severity="{sev}"}} {len(vals)}')

        lines.extend(
            [
                "# HELP postmortems_completed_total Completed blameless postmortems by severity.",
                "# TYPE postmortems_completed_total counter",
            ]
        )
        pm_counts: dict[str, int] = {}
        for rec in self._incidents.values():
            if rec.postmortem_completed:
                pm_counts[rec.severity.value] = pm_counts.get(rec.severity.value, 0) + 1
        for sev, val in sorted(pm_counts.items()):
            lines.append(f'postmortems_completed_total{{severity="{sev}"}} {val}')

        return "\n".join(lines) + "\n"

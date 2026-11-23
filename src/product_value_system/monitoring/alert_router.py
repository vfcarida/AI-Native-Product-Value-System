"""
AlertManager routing, inhibition, grouping, silencing, and privacy-safe dispatch (PROD-008).

Provides a deterministic local implementation of Prometheus AlertManager semantics
aligned directly with ``deploy/helm/dependencies/alertmanager-config.yaml`` so routing
rules, PagerDuty/Slack/Email fan-out, inhibition rules, maintenance silences, and
sensitive-data redaction are empirically verifiable without a live cluster.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from product_value_system.observability.trace_decorator import _redact_sensitive

_SENSITIVE_ANNOTATION_KEYS: frozenset[str] = frozenset(
    {"prompt", "completion", "api_key", "password", "secret", "token", "authorization"}
)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True)
class Alert:
    """Prometheus alert payload routed through AlertManager."""

    labels: dict[str, str]
    annotations: dict[str, str] = field(default_factory=dict)
    starts_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    generator_url: str = ""

    @property
    def alertname(self) -> str:
        return self.labels.get("alertname", "UnknownAlert")

    @property
    def severity(self) -> str:
        return self.labels.get("severity", "info")


@dataclass(frozen=True)
class SilenceMatcher:
    """AlertManager v2 silence label matcher (exact or regex)."""

    name: str
    value: str
    is_regex: bool = False
    is_equal: bool = True

    def matches(self, labels: Mapping[str, str]) -> bool:
        actual = labels.get(self.name, "")
        if self.is_regex:
            matched = re.fullmatch(self.value, actual) is not None
        else:
            matched = actual == self.value
        return matched if self.is_equal else not matched


@dataclass(frozen=True)
class Silence:
    """AlertManager v2 maintenance window silence."""

    silence_id: str
    matchers: tuple[SilenceMatcher, ...]
    starts_at: datetime
    ends_at: datetime
    created_by: str
    comment: str

    def is_active(self, now: datetime | None = None) -> bool:
        ref = _ensure_utc(now) if now else datetime.now(UTC)
        return _ensure_utc(self.starts_at) <= ref < _ensure_utc(self.ends_at)

    def suppresses(self, alert: Alert, now: datetime | None = None) -> bool:
        if not self.is_active(now):
            return False
        return all(m.matches(alert.labels) for m in self.matchers)

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "silenceID": self.silence_id,
            "matchers": [
                {
                    "name": m.name,
                    "value": m.value,
                    "isRegex": m.is_regex,
                    "isEqual": m.is_equal,
                }
                for m in self.matchers
            ],
            "startsAt": _ensure_utc(self.starts_at).isoformat(),
            "endsAt": _ensure_utc(self.ends_at).isoformat(),
            "createdBy": self.created_by,
            "comment": self.comment,
        }


def sanitize_alert_payload(alert: Alert) -> dict[str, Any]:
    """Redact API keys, emails, Bearer tokens, and prompt fields from alert notifications."""
    clean_labels: dict[str, str] = {}
    for k, v in alert.labels.items():
        if k.lower() in _SENSITIVE_ANNOTATION_KEYS:
            continue
        clean_labels[k] = _redact_sensitive(str(v))

    clean_annotations: dict[str, str] = {}
    for k, v in alert.annotations.items():
        if k.lower() in _SENSITIVE_ANNOTATION_KEYS:
            clean_annotations[k] = "[REDACTED]"
        else:
            clean_annotations[k] = _redact_sensitive(str(v))

    return {
        "labels": clean_labels,
        "annotations": clean_annotations,
        "startsAt": _ensure_utc(alert.starts_at).isoformat(),
        "generatorURL": alert.generator_url,
    }


def _route_matches(route: Mapping[str, Any], labels: Mapping[str, str]) -> bool:
    for key, expected in (route.get("match") or {}).items():
        if labels.get(key) != str(expected):
            return False
    for key, pattern in (route.get("match_re") or {}).items():
        if re.fullmatch(str(pattern), labels.get(key, "")) is None:
            return False
    return True


def _inhibit_matches(
    source_labels: Mapping[str, str],
    target_labels: Mapping[str, str],
    rule: Mapping[str, Any],
) -> bool:
    # Check source matchers
    for key, expected in (rule.get("source_match") or {}).items():
        if source_labels.get(key) != str(expected):
            return False
    for key, pattern in (rule.get("source_match_re") or {}).items():
        if re.fullmatch(str(pattern), source_labels.get(key, "")) is None:
            return False
    # Check target matchers
    for key, expected in (rule.get("target_match") or {}).items():
        if target_labels.get(key) != str(expected):
            return False
    for key, pattern in (rule.get("target_match_re") or {}).items():
        if re.fullmatch(str(pattern), target_labels.get(key, "")) is None:
            return False
    # Equal labels must match on both source and target
    for eq_label in rule.get("equal") or []:
        if source_labels.get(eq_label) != target_labels.get(eq_label):
            return False
    return True


class AlertRoutingEngine:
    """Evaluates AlertManager routing trees, inhibition rules, grouping, and silences."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        self.config = dict(config)
        self.root_route: dict[str, Any] = dict(self.config.get("route") or {})
        self.inhibit_rules: list[dict[str, Any]] = list(self.config.get("inhibit_rules") or [])
        self.receivers: dict[str, dict[str, Any]] = {
            str(r["name"]): dict(r) for r in (self.config.get("receivers") or []) if "name" in r
        }
        self._silences: list[Silence] = []

    @classmethod
    def from_config_yaml(cls, path: Path | str) -> AlertRoutingEngine:
        """Load AlertManager configuration from a ConfigMap or raw ``alertmanager.yml`` file."""
        docs = [d for d in yaml.safe_load_all(Path(path).read_text(encoding="utf-8")) if d]
        for doc in docs:
            if doc.get("kind") == "ConfigMap" and "alertmanager.yml" in (doc.get("data") or {}):
                raw_am = yaml.safe_load(doc["data"]["alertmanager.yml"])
                return cls(raw_am)
            if "route" in doc and "receivers" in doc:
                return cls(doc)
        raise ValueError(f"No AlertManager route/receivers configuration found in {path}")

    def route_alert(self, alert: Alert) -> list[str]:
        """Return ordered receiver names matched for ``alert`` (respecting ``continue``)."""
        matched: list[str] = []
        self._walk_route(self.root_route, alert.labels, matched)
        return matched

    def _walk_route(
        self,
        node: Mapping[str, Any],
        labels: Mapping[str, str],
        out: list[str],
    ) -> None:
        child_matched = False
        for child in node.get("routes") or []:
            if _route_matches(child, labels):
                child_matched = True
                if child.get("routes"):
                    self._walk_route(child, labels, out)
                else:
                    receiver = child.get("receiver")
                    if receiver:
                        out.append(str(receiver))
                if not child.get("continue", False):
                    break
        if not child_matched:
            default_receiver = node.get("receiver")
            if default_receiver:
                out.append(str(default_receiver))

    def apply_inhibitions(self, alerts: Sequence[Alert]) -> list[Alert]:
        """Filter out alerts inhibited by higher-severity firing source alerts."""
        active: list[Alert] = []
        for idx, target in enumerate(alerts):
            suppressed = False
            for src_idx, source in enumerate(alerts):
                if idx == src_idx:
                    continue
                for rule in self.inhibit_rules:
                    if _inhibit_matches(source.labels, target.labels, rule):
                        suppressed = True
                        break
                if suppressed:
                    break
            if not suppressed:
                active.append(target)
        return active

    def group_alerts(self, alerts: Sequence[Alert]) -> dict[tuple[str, ...], list[Alert]]:
        """Group alerts by ``route.group_by`` labels for batching and deduplication."""
        group_keys: list[str] = list(self.root_route.get("group_by") or ["alertname"])
        groups: dict[tuple[str, ...], list[Alert]] = {}
        for alert in alerts:
            key = tuple(alert.labels.get(label, "") for label in group_keys)
            groups.setdefault(key, []).append(alert)
        return groups

    def create_silence(
        self,
        matchers: Sequence[SilenceMatcher],
        *,
        duration: timedelta,
        created_by: str,
        comment: str,
        starts_at: datetime | None = None,
    ) -> Silence:
        """Register an AlertManager maintenance silence."""
        if duration.total_seconds() <= 0:
            raise ValueError("Silence duration must be positive")
        start = _ensure_utc(starts_at) if starts_at else datetime.now(UTC)
        silence = Silence(
            silence_id=str(uuid.uuid4()),
            matchers=tuple(matchers),
            starts_at=start,
            ends_at=start + duration,
            created_by=created_by,
            comment=comment,
        )
        self._silences.append(silence)
        return silence

    def is_silenced(self, alert: Alert, *, now: datetime | None = None) -> bool:
        """Return ``True`` if any active silence suppresses ``alert``."""
        return any(s.suppresses(alert, now=now) for s in self._silences)

    def dispatch(
        self,
        alerts: Sequence[Alert],
        *,
        sender: Callable[[str, dict[str, Any]], None],
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Inhibit, filter silences, group, sanitize, and dispatch alerts to receivers."""
        non_inhibited = self.apply_inhibitions(alerts)
        deliverable = [a for a in non_inhibited if not self.is_silenced(a, now=now)]
        grouped = self.group_alerts(deliverable)

        dispatched: list[dict[str, Any]] = []
        for group_key, group_alerts in grouped.items():
            # Route based on representative alert labels for the group
            receivers = self.route_alert(group_alerts[0])
            sanitized = [sanitize_alert_payload(a) for a in group_alerts]
            for receiver in receivers:
                payload = {
                    "receiver": receiver,
                    "group_key": list(group_key),
                    "alert_count": len(sanitized),
                    "alerts": sanitized,
                    "common_annotations": sanitized[0]["annotations"],
                }
                sender(receiver, payload)
                dispatched.append(payload)
        return dispatched


__all__ = [
    "Alert",
    "AlertRoutingEngine",
    "Silence",
    "SilenceMatcher",
    "sanitize_alert_payload",
]

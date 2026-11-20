"""
Feature flag evaluation service with consistent SHA-256 hashing and targeting rules (EVO-015).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_value_system.feature_flags.models import (
    AuditEntry,
    FeatureFlag,
    FlagEvaluation,
    RolloutStage,
)
from product_value_system.feature_flags.storage import FlagStorage

logger = logging.getLogger(__name__)


class FeatureFlagService:
    """
    Evaluates feature flags for user/request contexts using consistent hashing
    so a given user receives stable assignment across requests.
    """

    def __init__(
        self,
        flags: dict[str, FeatureFlag] | None = None,
        *,
        storage_path: Path | str | None = None,
        max_history: int = 5000,
    ) -> None:
        self._lock = threading.RLock()
        self.storage: FlagStorage | None = FlagStorage(storage_path) if storage_path else None
        if flags is not None:
            self.flags: dict[str, FeatureFlag] = dict(flags)
        elif self.storage is not None and self.storage.path.exists():
            self.flags = self.storage.load()
        else:
            self.flags = {}
        self._evaluations: deque[FlagEvaluation] = deque(maxlen=max_history)
        self._audit_log: deque[AuditEntry] = deque(maxlen=max_history)

    @classmethod
    def from_yaml(cls, path: Path | str) -> FeatureFlagService:
        """Instantiate service from a schema-validated ``flags.yaml`` file."""
        storage = FlagStorage(path)
        flags = storage.load()
        return cls(flags=flags, storage_path=path)

    def reload(self) -> dict[str, FeatureFlag]:
        """Reload flags from configured YAML storage."""
        if self.storage is None:
            return self.flags
        loaded = self.storage.load()
        with self._lock:
            self.flags = loaded
        return self.flags

    def save(self) -> None:
        """Persist current flags to configured YAML storage (no-op if in-memory)."""
        if self.storage is None:
            return
        with self._lock:
            self.storage.save(self.flags)

    def get_flag(self, flag_name: str) -> FeatureFlag | None:
        """Retrieve a flag definition by name."""
        with self._lock:
            return self.flags.get(flag_name)

    def list_flags(self) -> list[FeatureFlag]:
        """Return all configured feature flags."""
        with self._lock:
            return list(self.flags.values())

    def get_evaluations(self, limit: int = 100) -> list[FlagEvaluation]:
        """Return most recent flag evaluation records."""
        with self._lock:
            items = list(self._evaluations)
        return items[-limit:]

    def get_audit_log(self, limit: int = 100) -> list[AuditEntry]:
        """Return most recent mutation audit entries."""
        with self._lock:
            items = list(self._audit_log)
        return items[-limit:]

    def record_audit(
        self,
        action: str,
        flag_name: str,
        actor: str = "system",
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Append an immutable audit log record for flag changes."""
        entry = AuditEntry(
            action=action,
            flag_name=flag_name,
            actor=actor,
            details=details or {},
        )
        with self._lock:
            self._audit_log.append(entry)
        return entry

    def toggle_flag(self, flag_name: str, *, actor: str = "system") -> FeatureFlag:
        """Flip a flag's master enabled switch, persist, and audit-log the change."""
        with self._lock:
            flag = self.flags.get(flag_name)
            if flag is None:
                raise KeyError(f"Flag not found: {flag_name}")
            previous = flag.enabled
            flag.enabled = not flag.enabled
            flag.updated_at = datetime.now(UTC)
            self.save()
            self.record_audit(
                action="toggle",
                flag_name=flag_name,
                actor=actor,
                details={"previous_enabled": previous, "enabled": flag.enabled},
            )
            return flag

    def set_rollout(
        self,
        flag_name: str,
        *,
        percentage: float | None = None,
        stage: RolloutStage | str | None = None,
        actor: str = "system",
    ) -> FeatureFlag:
        """Update a flag's rollout percentage and/or stage, persist, and audit-log."""
        with self._lock:
            flag = self.flags.get(flag_name)
            if flag is None:
                raise KeyError(f"Flag not found: {flag_name}")
            prev_pct = flag.rollout_percentage
            prev_stage = flag.rollout_stage.value

            if stage is not None:
                flag.rollout_stage = (
                    stage if isinstance(stage, RolloutStage) else RolloutStage(stage)
                )
            if percentage is not None:
                flag.rollout_percentage = round(max(0.0, min(1.0, float(percentage))), 6)

            flag.updated_at = datetime.now(UTC)
            self.save()
            self.record_audit(
                action="update_rollout",
                flag_name=flag_name,
                actor=actor,
                details={
                    "previous_percentage": prev_pct,
                    "rollout_percentage": flag.rollout_percentage,
                    "previous_stage": prev_stage,
                    "rollout_stage": flag.rollout_stage.value,
                },
            )
            return flag

    def is_enabled(
        self,
        flag_name: str,
        user_id: str | None = None,
        request_context: dict[str, Any] | None = None,
    ) -> FlagEvaluation:
        """
        Evaluate whether ``flag_name`` is enabled for ``user_id`` and ``request_context``.

        Evaluation order:
        1. Flag existence check
        2. Master ``enabled`` switch check
        3. Ordered ``targeting_rules`` (user allowlist, environment, attributes, percentage)
        4. Fallback percentage rollout using consistent SHA-256 bucketing
        """
        ctx = request_context or {}
        request_id = str(ctx["request_id"]) if ctx.get("request_id") is not None else None

        with self._lock:
            flag = self.flags.get(flag_name)

        if flag is None:
            return self._record_eval(
                FlagEvaluation(
                    flag_name=flag_name,
                    enabled=False,
                    reason="Flag not found",
                    user_id=user_id,
                    request_id=request_id,
                )
            )

        if not flag.enabled:
            return self._record_eval(
                FlagEvaluation(
                    flag_name=flag_name,
                    enabled=False,
                    reason="Flag disabled (master switch off)",
                    user_id=user_id,
                    request_id=request_id,
                )
            )

        # Evaluate targeting rules in declared order
        for rule in flag.targeting_rules:
            if self._evaluate_rule(flag.name, rule, user_id, ctx):
                return self._record_eval(
                    FlagEvaluation(
                        flag_name=flag_name,
                        enabled=True,
                        reason=f"Matched targeting rule: {rule['type']}",
                        user_id=user_id,
                        request_id=request_id,
                    )
                )

        # Fallback: percentage-based rollout
        if flag.rollout_percentage >= 1.0:
            return self._record_eval(
                FlagEvaluation(
                    flag_name=flag_name,
                    enabled=True,
                    reason=f"In rollout percentage ({flag.rollout_percentage:.0%})",
                    user_id=user_id,
                    request_id=request_id,
                )
            )

        identity = user_id or request_id
        if (
            flag.rollout_percentage > 0.0
            and identity is not None
            and self._is_in_rollout_percentage(flag_name, identity, flag.rollout_percentage)
        ):
            return self._record_eval(
                FlagEvaluation(
                    flag_name=flag_name,
                    enabled=True,
                    reason=f"In rollout percentage ({flag.rollout_percentage:.0%})",
                    user_id=user_id,
                    request_id=request_id,
                )
            )

        return self._record_eval(
            FlagEvaluation(
                flag_name=flag_name,
                enabled=False,
                reason="Not in rollout percentage",
                user_id=user_id,
                request_id=request_id,
            )
        )

    def _record_eval(self, evaluation: FlagEvaluation) -> FlagEvaluation:
        with self._lock:
            self._evaluations.append(evaluation)
        return evaluation

    def _evaluate_rule(
        self,
        flag_name: str,
        rule: dict[str, Any],
        user_id: str | None,
        request_context: dict[str, Any],
    ) -> bool:
        """Evaluate a single targeting rule against user and request context."""
        rule_type = str(rule.get("type", ""))
        operator = str(rule.get("operator", "equals"))

        if rule_type == "user_id":
            if user_id is None:
                return False
            values = rule.get("values", [])
            if operator == "not_in":
                return user_id not in values
            if operator == "equals":
                return user_id == rule.get("value")
            return user_id in values

        if rule_type == "environment":
            env = request_context.get("environment")
            if env is None:
                return False
            if operator == "in":
                return env in rule.get("values", [])
            if operator == "not_equals":
                return bool(env != rule.get("value"))
            return bool(env == rule.get("value"))

        if rule_type == "attribute":
            attr = str(rule.get("attribute", ""))
            if not attr or attr not in request_context:
                return False
            actual = request_context[attr]
            if operator == "in":
                return actual in rule.get("values", [])
            if operator == "not_in":
                return actual not in rule.get("values", [])
            if operator == "not_equals":
                return bool(actual != rule.get("value"))
            return bool(actual == rule.get("value"))

        if rule_type == "percentage":
            pct = float(rule.get("value", 0.0))
            identity = user_id or str(request_context.get("request_id") or "")
            if not identity:
                return False
            return self._is_in_rollout_percentage(flag_name, identity, pct)

        return False

    def _is_in_rollout_percentage(
        self,
        flag_name: str,
        user_id: str,
        percentage: float,
    ) -> bool:
        """
        Consistent SHA-256 hashing determining whether ``user_id`` falls inside
        ``percentage`` (0.01% granularity via modulo 10,000 bucketing).
        """
        if percentage <= 0.0:
            return False
        if percentage >= 1.0:
            return True
        hash_input = f"{flag_name}:{user_id}".encode()
        hash_value = int(hashlib.sha256(hash_input).hexdigest(), 16)
        bucket = hash_value % 10000
        threshold = int(round(percentage * 10000))
        return bucket < threshold

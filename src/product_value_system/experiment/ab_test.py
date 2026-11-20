"""
A/B testing framework with consistent hashing, statistical tests, guardrails,
and stopping criteria (EVO-016).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.stats import chi2_contingency, norm, ttest_ind

from product_value_system.experiment.models import (
    ExperimentDesign,
    ExperimentResult,
    ExperimentStatus,
    StatisticalTestType,
    Variant,
)


class ABTest:
    """
    Online controlled experiment (A/B test) engine.

    Privacy: User identifiers are hashed via SHA-256 and never persisted in raw form.
    Performance: Variant assignment runs in <1ms; analysis of N=10,000 completes in <1s.
    """

    def __init__(
        self,
        experiment_design: ExperimentDesign,
        *,
        alert_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self.design = experiment_design
        self.data: dict[str, list[float]] = {
            Variant.CONTROL.value: [],
            Variant.TREATMENT.value: [],
        }
        self._guardrail_observations: dict[str, dict[str, list[float]]] = {}
        self._guardrail_data: dict[str, dict[str, float]] = {}
        self.status: ExperimentStatus = ExperimentStatus.DRAFT
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.alert_callback = alert_callback
        self.alerts_sent: list[str] = []
        self.baseline_gate_result: Any | None = None

    def start(
        self,
        *,
        started_at: datetime | None = None,
        baseline: Any | None = None,
        validation_gate: Any | None = None,
        auto_validate: bool = False,
        strict_validation: bool = True,
        effect_size_percent: float | None = None,
    ) -> None:
        """
        Transition experiment from DRAFT to RUNNING.

        When ``auto_validate=True`` (or ``baseline`` is supplied), runs EVO-019
        ``ValidationGate`` before starting and raises ``ValueError`` if blocked.
        """
        if auto_validate or baseline is not None:
            if baseline is None:
                raise ValueError("baseline data is required when auto_validate=True")
            if validation_gate is None:
                from product_value_system.validation.gate import ValidationGate

                validation_gate = ValidationGate()
            effect_pct = (
                effect_size_percent
                if effect_size_percent is not None
                else self.design.hypothesis.minimum_detectable_effect * 100.0
            )
            gate_result = validation_gate.validate_baseline(
                baseline,
                effect_size_percent=effect_pct,
                strict=strict_validation,
            )
            self.baseline_gate_result = gate_result
            if not gate_result.passed and not gate_result.can_proceed_with_warning:
                raise ValueError(f"Baseline validation failed:\n{gate_result.summary}")

        self.status = ExperimentStatus.RUNNING
        self.started_at = started_at or datetime.now(UTC)

    def complete(self, *, completed_at: datetime | None = None) -> None:
        """Mark experiment as COMPLETED."""
        self.status = ExperimentStatus.COMPLETED
        self.completed_at = completed_at or datetime.now(UTC)

    def abort(self, reason: str, *, aborted_at: datetime | None = None) -> None:
        """Abort experiment immediately (e.g., guardrail violation) and alert team."""
        self.status = ExperimentStatus.ABORTED
        self.completed_at = aborted_at or datetime.now(UTC)
        msg = f"Experiment '{self.design.id}' aborted: {reason}"
        self.alerts_sent.append(msg)
        if self.alert_callback is not None:
            self.alert_callback(msg)

    def assign_variant(self, user_id: str) -> Variant:
        """
        Deterministically assign ``user_id`` to CONTROL or TREATMENT (50/50 split)
        via SHA-256 consistent hashing.
        """
        hash_input = f"{self.design.id}:{user_id}".encode()
        hash_value = int(hashlib.sha256(hash_input).hexdigest(), 16)
        return Variant.CONTROL if hash_value % 2 == 0 else Variant.TREATMENT

    def record_metric(self, user_id: str, metric_value: float) -> Variant:
        """Record a primary metric observation for ``user_id`` (user ID is not retained)."""
        variant = self.assign_variant(user_id)
        self.data[variant.value].append(float(metric_value))
        return variant

    def record_guardrail(self, guardrail_name: str, user_id: str, value: float) -> Variant:
        """Record a per-user guardrail metric observation without storing raw user ID."""
        variant = self.assign_variant(user_id)
        bucket = self._guardrail_observations.setdefault(
            guardrail_name,
            {Variant.CONTROL.value: [], Variant.TREATMENT.value: []},
        )
        bucket[variant.value].append(float(value))
        return variant

    def set_guardrail_summary(
        self,
        guardrail_name: str,
        *,
        control_value: float,
        treatment_value: float,
    ) -> None:
        """Supply pre-aggregated guardrail metric values from an external analytics backend."""
        self._guardrail_data[guardrail_name] = {
            Variant.CONTROL.value: float(control_value),
            Variant.TREATMENT.value: float(treatment_value),
        }

    def analyze(
        self,
        test_type: StatisticalTestType | str | None = None,
        *,
        bayesian_draws: int = 10000,
        seed: int = 42,
    ) -> ExperimentResult:
        """
        Analyze experiment observations using the selected statistical method:
        ``ttest`` (Welch's two-sample t-test), ``chi_square`` (2x2 contingency test),
        or ``bayesian`` (Beta-Binomial / Normal-Normal posterior simulation).
        """
        resolved_type = (
            StatisticalTestType(test_type) if test_type is not None else self.design.test_type
        )
        control_arr = np.asarray(self.data[Variant.CONTROL.value], dtype=float)
        treatment_arr = np.asarray(self.data[Variant.TREATMENT.value], dtype=float)

        if len(control_arr) < 2 or len(treatment_arr) < 2:
            raise ValueError(
                "At least 2 observations per variant are required for statistical analysis "
                f"(control={len(control_arr)}, treatment={len(treatment_arr)})"
            )

        control_mean = float(np.mean(control_arr))
        treatment_mean = float(np.mean(treatment_arr))
        control_stddev = float(np.std(control_arr, ddof=1))
        treatment_stddev = float(np.std(treatment_arr, ddof=1))

        denom = control_mean if abs(control_mean) > 1e-12 else 1.0
        lift = (treatment_mean - control_mean) / denom
        alpha = 1.0 - self.design.hypothesis.confidence_level
        z_critical = float(norm.ppf(1.0 - alpha / 2.0))

        se_diff = math.sqrt(
            (control_stddev**2 / len(control_arr)) + (treatment_stddev**2 / len(treatment_arr))
        )
        lift_se = se_diff / abs(denom)
        ci_lower = lift - z_critical * lift_se
        ci_upper = lift + z_critical * lift_se

        bayesian_prob: float | None = None
        bayesian_ci: tuple[float, float] | None = None

        if resolved_type == StatisticalTestType.CHI_SQUARE:
            p_value = self._compute_chi_square_pvalue(control_arr, treatment_arr)
            statistically_significant = p_value < alpha
        elif resolved_type == StatisticalTestType.BAYESIAN:
            bayesian_prob, bayesian_ci = self._compute_bayesian_posterior(
                control_arr,
                treatment_arr,
                confidence_level=self.design.hypothesis.confidence_level,
                num_draws=bayesian_draws,
                seed=seed,
            )
            ci_lower, ci_upper = bayesian_ci
            # Posterior tail probability analogous to two-sided p-value
            p_value = max(0.0, min(1.0, 2.0 * min(bayesian_prob, 1.0 - bayesian_prob)))
            statistically_significant = bayesian_prob >= self.design.hypothesis.confidence_level
        else:
            p_value = self._compute_ttest_pvalue(control_arr, treatment_arr)
            statistically_significant = p_value < alpha

        mde = self.design.hypothesis.minimum_detectable_effect
        practically_significant = lift >= mde
        guardrail_violations = self._check_guardrails()

        if guardrail_violations:
            recommendation = "reject"
            reasoning = f"{len(guardrail_violations)} guardrail(s) violated"
        elif statistically_significant and practically_significant:
            recommendation = "adopt"
            reasoning = f"Lift {lift:.1%} is statistically and practically significant"
        elif not statistically_significant:
            recommendation = "continue"
            reasoning = f"Not statistically significant (p={p_value:.4f})"
        else:
            recommendation = "reject"
            reasoning = f"Lift {lift:.1%} below MDE {mde:.1%}"

        return ExperimentResult(
            experiment_id=self.design.id,
            status=self.status,
            control_samples=len(control_arr),
            treatment_samples=len(treatment_arr),
            control_mean=control_mean,
            treatment_mean=treatment_mean,
            control_stddev=control_stddev,
            treatment_stddev=treatment_stddev,
            lift=lift,
            confidence_interval=(ci_lower, ci_upper),
            p_value=p_value,
            statistically_significant=statistically_significant,
            minimum_detectable_effect=mde,
            practically_significant=practically_significant,
            recommendation=recommendation,
            reasoning=reasoning,
            guardrail_violations=guardrail_violations,
            test_type=resolved_type.value,
            bayesian_prob_treatment_beats_control=bayesian_prob,
            bayesian_credible_interval=bayesian_ci,
            completed_at=(
                self.completed_at or datetime.now(UTC)
                if self.status
                in {
                    ExperimentStatus.COMPLETED,
                    ExperimentStatus.STOPPED_EARLY,
                    ExperimentStatus.ABORTED,
                }
                else None
            ),
        )

    def _compute_ttest_pvalue(self, control: np.ndarray, treatment: np.ndarray) -> float:
        """Compute two-sample Welch's t-test p-value with zero-variance protection."""
        if np.allclose(control, control[0]) and np.allclose(treatment, treatment[0]):
            return 1.0 if math.isclose(float(control[0]), float(treatment[0])) else 0.0
        _, p_val = ttest_ind(treatment, control, equal_var=False)
        if math.isnan(float(p_val)):
            return 1.0
        return float(p_val)

    def _compute_chi_square_pvalue(self, control: np.ndarray, treatment: np.ndarray) -> float:
        """Compute Pearson chi-square p-value for binary conversion observations (0/1)."""
        c_success = int(np.sum(control > 0.0))
        c_fail = int(len(control) - c_success)
        t_success = int(np.sum(treatment > 0.0))
        t_fail = int(len(treatment) - t_success)

        table = np.array([[c_success, c_fail], [t_success, t_fail]], dtype=float)
        if np.any(table.sum(axis=0) == 0) or np.any(table.sum(axis=1) == 0):
            return 1.0
        _, p_val, _, _ = chi2_contingency(table, correction=False)
        return float(p_val)

    def _compute_bayesian_posterior(
        self,
        control: np.ndarray,
        treatment: np.ndarray,
        *,
        confidence_level: float,
        num_draws: int,
        seed: int,
    ) -> tuple[float, tuple[float, float]]:
        """
        Compute ``P(treatment > control)`` and equal-tailed credible interval for relative lift.

        Uses exact conjugate Beta-Binomial simulation for binary metrics (0/1) and
        Normal-Normal posterior simulation for continuous metrics.
        """
        rng = np.random.default_rng(seed)
        is_binary = bool(
            np.all(np.isin(control, [0.0, 1.0])) and np.all(np.isin(treatment, [0.0, 1.0]))
        )
        tail = (1.0 - confidence_level) / 2.0

        if is_binary:
            c_succ = float(np.sum(control))
            c_fail = float(len(control)) - c_succ
            t_succ = float(np.sum(treatment))
            t_fail = float(len(treatment)) - t_succ
            c_samples = rng.beta(1.0 + c_succ, 1.0 + c_fail, size=num_draws)
            t_samples = rng.beta(1.0 + t_succ, 1.0 + t_fail, size=num_draws)
        else:
            c_mean = float(np.mean(control))
            t_mean = float(np.mean(treatment))
            c_se = max(1e-9, float(np.std(control, ddof=1)) / math.sqrt(len(control)))
            t_se = max(1e-9, float(np.std(treatment, ddof=1)) / math.sqrt(len(treatment)))
            c_samples = rng.normal(c_mean, c_se, size=num_draws)
            t_samples = rng.normal(t_mean, t_se, size=num_draws)

        prob_win = float(np.mean(t_samples > c_samples))
        safe_denom = np.where(np.abs(c_samples) > 1e-12, c_samples, 1.0)
        lift_draws = (t_samples - c_samples) / safe_denom
        ci_lo = float(np.quantile(lift_draws, tail))
        ci_hi = float(np.quantile(lift_draws, 1.0 - tail))
        return prob_win, (ci_lo, ci_hi)

    def _get_guardrail_metric(self, guardrail_name: str, variant: Variant) -> float | None:
        """Fetch guardrail mean from pre-aggregated summary or recorded observations."""
        if guardrail_name in self._guardrail_data:
            return self._guardrail_data[guardrail_name].get(variant.value)
        obs = self._guardrail_observations.get(guardrail_name, {}).get(variant.value, [])
        if obs:
            return float(np.mean(obs))
        return None

    def _check_guardrails(self) -> list[dict[str, Any]]:
        """Evaluate all configured guardrails and return list of threshold violations."""
        violations: list[dict[str, Any]] = []

        for guardrail in self.design.guardrails:
            control_val = self._get_guardrail_metric(guardrail.name, Variant.CONTROL)
            treatment_val = self._get_guardrail_metric(guardrail.name, Variant.TREATMENT)
            if control_val is None or treatment_val is None or abs(control_val) <= 1e-12:
                continue

            change = (treatment_val - control_val) / abs(control_val)
            violated = (
                (guardrail.direction == "decrease" and change < guardrail.threshold)
                or (guardrail.direction == "increase" and change > guardrail.threshold)
            )
            if violated:
                violation = {
                    "guardrail": guardrail.name,
                    "threshold": guardrail.threshold,
                    "direction": guardrail.direction,
                    "actual": round(change, 6),
                    "control_value": round(control_val, 6),
                    "treatment_value": round(treatment_val, 6),
                    "description": guardrail.description,
                }
                violations.append(violation)
                msg = (
                    f"Guardrail '{guardrail.name}' violated on '{self.design.id}': "
                    f"change={change:+.2%} (threshold={guardrail.threshold:+.2%})"
                )
                self.alerts_sent.append(msg)
                if self.alert_callback is not None:
                    self.alert_callback(msg)

        return violations

    def should_stop_early(self, *, now: datetime | None = None) -> tuple[bool, str]:
        """
        Evaluate stopping criteria:
        1. Guardrail violation -> immediate stop (abort)
        2. Max duration reached -> stop
        3. Early stopping on strong evidence (``p < 0.01`` & practically significant)
        """
        criteria = self.design.stopping_criteria or {}

        # 1. Guardrails take highest priority (prevent user/business harm immediately)
        guardrail_violations = self._check_guardrails()
        if guardrail_violations:
            name = guardrail_violations[0]["guardrail"]
            if criteria.get("guardrail_violation_action", "abort") == "abort":
                self.status = ExperimentStatus.ABORTED
                self.completed_at = now or datetime.now(UTC)
            return True, f"Guardrail violated: {name}"

        # 2. Max duration check
        max_days = int(criteria.get("max_duration_days", self.design.duration_days))
        if self.started_at is not None:
            current_time = now or datetime.now(UTC)
            elapsed_days = (current_time - self.started_at).days
            if elapsed_days >= max_days:
                self.status = ExperimentStatus.COMPLETED
                self.completed_at = current_time
                return True, f"Max duration reached ({max_days} days)"

        # 3. Statistical early stopping (stricter alpha = 0.01)
        early_stop_enabled = bool(criteria.get("early_stop_enabled", True))
        early_alpha = float(criteria.get("early_stop_alpha", 0.01))
        if (
            early_stop_enabled
            and len(self.data[Variant.CONTROL.value]) >= 2
            and len(self.data[Variant.TREATMENT.value]) >= 2
        ):
            result = self.analyze()
            if result.p_value < early_alpha and result.practically_significant:
                self.status = ExperimentStatus.STOPPED_EARLY
                self.completed_at = now or datetime.now(UTC)
                return (
                    True,
                    f"Sufficient evidence (p < {early_alpha:.2f}, practically significant)",
                )

        return False, ""

    def to_state_dict(self) -> dict[str, Any]:
        """Serialize experiment design and anonymized variant observations."""
        return {
            "design": self.design.to_dict(),
            "status": self.status.value,
            "started_at": (
                self.started_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.started_at
                else None
            ),
            "completed_at": (
                self.completed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
                if self.completed_at
                else None
            ),
            "data": {
                Variant.CONTROL.value: list(self.data[Variant.CONTROL.value]),
                Variant.TREATMENT.value: list(self.data[Variant.TREATMENT.value]),
            },
            "guardrail_data": dict(self._guardrail_data),
        }

    def save_state(self, path: Path | str) -> Path:
        """Persist anonymized experiment state to JSON or YAML."""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_state_dict()
        if out_path.suffix.lower() == ".json":
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            out_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return out_path

    @classmethod
    def load_state(cls, path: Path | str) -> ABTest:
        """Reconstruct an ``ABTest`` instance from a saved JSON or YAML file."""
        in_path = Path(path)
        raw = in_path.read_text(encoding="utf-8")
        payload = json.loads(raw) if in_path.suffix.lower() == ".json" else yaml.safe_load(raw)
        design = ExperimentDesign.from_dict(payload["design"])
        ab_test = cls(design)
        ab_test.status = ExperimentStatus(payload.get("status", ExperimentStatus.DRAFT.value))
        if payload.get("started_at"):
            ab_test.started_at = datetime.fromisoformat(
                str(payload["started_at"]).replace("Z", "+00:00")
            )
        if payload.get("completed_at"):
            ab_test.completed_at = datetime.fromisoformat(
                str(payload["completed_at"]).replace("Z", "+00:00")
            )
        data = payload.get("data", {})
        ab_test.data[Variant.CONTROL.value] = [float(x) for x in data.get("control", [])]
        ab_test.data[Variant.TREATMENT.value] = [float(x) for x in data.get("treatment", [])]
        for g_name, g_vals in payload.get("guardrail_data", {}).items():
            ab_test.set_guardrail_summary(
                g_name,
                control_value=float(g_vals["control"]),
                treatment_value=float(g_vals["treatment"]),
            )
        return ab_test

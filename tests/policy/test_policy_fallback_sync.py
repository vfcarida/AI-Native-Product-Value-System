"""
Automated Cedar policy vs. Python fallback synchronization tests (EVO-005).
"""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from product_value_system.policy.engine import AuthorizationDecision, Decision, PolicyEngine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate-policy-sync.py"


def _load_validator_module() -> Any:
    """Dynamically load scripts/validate-policy-sync.py as a module."""
    spec = importlib.util.spec_from_file_location("validate_policy_sync", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["validate_policy_sync"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.policy_validation
class TestPolicyFallbackSync:
    """Verify policies/agent_authorization.cedar stays 100% synced with Python fallback."""

    @pytest.fixture
    def validator(self) -> Any:
        module = _load_validator_module()
        return module.PolicyValidator()

    @pytest.mark.asyncio
    async def test_all_policies_match(self, validator: Any) -> None:
        """All >=20 authorization test cases must agree between Cedar and Python fallback."""
        results = await validator.validate_all()
        report = validator.report(results)

        assert report["summary"]["total_tests"] >= 20, (
            f"Insufficient policy test coverage: {report['summary']['total_tests']} (< 20)"
        )
        assert report["summary"]["mismatches"] == 0, (
            f"Found {report['summary']['mismatches']} policy mismatches:\n"
            + "\n".join(
                f"- {m['description']}: Cedar={m['cedar']}, Fallback={m['fallback']}"
                for m in report["mismatches"]
            )
        )
        assert report["summary"]["pass_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_detects_intentional_fallback_drift(self) -> None:
        """Verify PolicyValidator catches drift if Python fallback diverges from Cedar."""
        from datetime import UTC, datetime

        module = _load_validator_module()

        class DriftedPolicyEngine(PolicyEngine):
            def _fallback_evaluate(self, request: dict[str, Any]) -> AuthorizationDecision:
                # Intentional bug: always ALLOW everything
                return AuthorizationDecision(
                    decision=Decision.ALLOW,
                    reason="Intentional drift",
                    policies_matched=["drifted"],
                    timestamp=datetime.now(UTC),
                )

        drifted_validator = module.PolicyValidator(
            engine=DriftedPolicyEngine(policies_dir=REPO_ROOT / "policies")
        )
        results = await drifted_validator.validate_all()
        report = drifted_validator.report(results)

        assert report["summary"]["mismatches"] > 0
        assert report["summary"]["pass_rate"] < 1.0

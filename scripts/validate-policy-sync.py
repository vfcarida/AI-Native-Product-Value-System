#!/usr/bin/env python3
"""
Validate Cedar policies match Python fallback logic in PolicyEngine.

Evaluates a comprehensive suite of authorization test cases against both:
1. Authoritative Cedar policies (``policies/agent_authorization.cedar``), using
   the Cedar CLI binary when installed (required with ``--require-cedar-cli``) or
   an independent Cedar policy file parser when running locally without the CLI.
2. ``PolicyEngine._fallback_evaluate()`` in ``src/product_value_system/policy/engine.py``.

Usage:
    python scripts/validate-policy-sync.py
    python scripts/validate-policy-sync.py --verbose
    python scripts/validate-policy-sync.py --output policy-validation-report.json
    python scripts/validate-policy-sync.py --require-cedar-cli
"""

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Ensure src/ is importable when running script directly
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from product_value_system.policy.engine import Decision, PolicyEngine  # noqa: E402


@dataclass
class TestCase:
    """Authorization test case for policy/fallback sync validation."""

    principal: dict[str, Any]
    action: str
    resource: dict[str, Any]
    context: dict[str, Any]
    description: str


@dataclass
class ValidationResult:
    """Result of validating one test case across Cedar and Python fallback."""

    test_case: TestCase
    cedar_decision: str
    fallback_decision: str
    match: bool
    cedar_reason: str = ""
    fallback_reason: str = ""
    evaluator_mode: str = "cedar_parser"


@dataclass
class ParsedCedarStatement:
    """Parsed Cedar permit/forbid statement from a .cedar policy file."""

    index: int
    effect: str  # "permit" or "forbid"
    principal_role: str | None  # None means any principal
    actions: set[str] | None  # None means any action
    resource_type: str | None  # e.g., "Tool", "Environment"
    resource_id: str | None  # None means any resource
    condition_kind: str | None  # "when", "unless", or None
    condition_expr: str | None


class CedarPolicyParser:
    """
    Independent parser and evaluator for Cedar policy files (.cedar).

    Directly parses ``policies/agent_authorization.cedar`` so policy drift
    between the .cedar file and ``PolicyEngine._fallback_evaluate()`` is
    detected immediately even in environments where the Rust Cedar CLI is
    not installed.
    """

    _STMT_RE = re.compile(
        r"(permit|forbid)\s*\((.*?)\)\s*(?:(when|unless)\s*\{(.*?)\})?\s*;",
        re.DOTALL,
    )
    _PRINCIPAL_ROLE_RE = re.compile(
        r'principal\s+in\s+ProductValueSystem::AgentRole::"([^"]+)"'
    )
    _ACTION_EQ_RE = re.compile(
        r'action\s*==\s*ProductValueSystem::Action::"([^"]+)"'
    )
    _ACTION_IN_RE = re.compile(r"action\s+in\s*\[(.*?)\]", re.DOTALL)
    _ACTION_ITEM_RE = re.compile(r'ProductValueSystem::Action::"([^"]+)"')
    _RESOURCE_IN_RE = re.compile(
        r'resource\s+in\s+ProductValueSystem::([A-Za-z_]+)::"([^"]+)"'
    )

    def __init__(self, policies_text: str) -> None:
        self.statements = self._parse(policies_text)

    def _strip_comments(self, text: str) -> str:
        """Remove // single-line comments."""
        return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())

    def _parse(self, policies_text: str) -> list[ParsedCedarStatement]:
        cleaned = self._strip_comments(policies_text)
        statements: list[ParsedCedarStatement] = []

        for idx, match in enumerate(self._STMT_RE.finditer(cleaned), start=1):
            effect = match.group(1)
            scope = match.group(2)
            cond_kind = match.group(3)
            cond_expr = match.group(4).strip() if match.group(4) else None

            p_match = self._PRINCIPAL_ROLE_RE.search(scope)
            principal_role = p_match.group(1) if p_match else None

            actions: set[str] | None = None
            a_eq = self._ACTION_EQ_RE.search(scope)
            a_in = self._ACTION_IN_RE.search(scope)
            if a_eq:
                actions = {a_eq.group(1)}
            elif a_in:
                actions = set(self._ACTION_ITEM_RE.findall(a_in.group(1)))

            r_match = self._RESOURCE_IN_RE.search(scope)
            resource_type = r_match.group(1) if r_match else None
            resource_id = r_match.group(2) if r_match else None

            statements.append(
                ParsedCedarStatement(
                    index=idx,
                    effect=effect,
                    principal_role=principal_role,
                    actions=actions,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    condition_kind=cond_kind,
                    condition_expr=cond_expr,
                )
            )

        return statements

    def _resolve_operand(
        self, token: str, principal: dict[str, Any], context: dict[str, Any]
    ) -> Any:
        raw = token.strip()
        if raw == "true":
            return True
        if raw == "false":
            return False
        if raw.startswith('"') and raw.endswith('"'):
            return raw[1:-1]
        if raw.startswith("context."):
            attr = raw.split(".", 1)[1]
            return context.get(attr)
        if raw.startswith("principal."):
            attr = raw.split(".", 1)[1]
            if attr in principal:
                return principal[attr]
            if attr == "trustLevel":
                return principal.get("trust_level", "medium")
            if attr == "costBudget":
                return principal.get("cost_budget", 100.0)
            return None
        try:
            return float(raw) if "." in raw else int(raw)
        except ValueError:
            return raw

    def _eval_condition(
        self,
        expr: str,
        principal: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        clauses = [c.strip() for c in expr.split("&&") if c.strip()]
        for clause in clauses:
            op_match = re.match(r"(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)", clause)
            if not op_match:
                return False
            lhs = self._resolve_operand(op_match.group(1), principal, context)
            op = op_match.group(2)
            rhs = self._resolve_operand(op_match.group(3), principal, context)

            if op == "==":
                ok = lhs == rhs
            elif op == "!=":
                ok = lhs != rhs
            elif op in {">=", "<=", ">", "<"}:
                if lhs is None or rhs is None:
                    ok = False
                elif op == ">=":
                    ok = float(lhs) >= float(rhs)
                elif op == "<=":
                    ok = float(lhs) <= float(rhs)
                elif op == ">":
                    ok = float(lhs) > float(rhs)
                else:
                    ok = float(lhs) < float(rhs)
            else:
                ok = False

            if not ok:
                return False
        return True

    def _matches_statement(
        self,
        stmt: ParsedCedarStatement,
        principal: dict[str, Any],
        action: str,
        resource: dict[str, Any],
        context: dict[str, Any],
    ) -> bool:
        if stmt.principal_role is not None:
            if principal.get("role") != stmt.principal_role:
                return False

        if stmt.actions is not None and action not in stmt.actions:
            return False

        if stmt.resource_id is not None:
            res_id = str(resource.get("name", resource.get("id", "")))
            res_type = str(resource.get("type", ""))
            if stmt.resource_type == "Environment":
                if res_id != stmt.resource_id and context.get("environment") != stmt.resource_id:
                    return False
            else:
                if res_id != stmt.resource_id:
                    return False
                if stmt.resource_type and res_type and res_type != stmt.resource_type:
                    return False

        if stmt.condition_kind and stmt.condition_expr:
            cond_val = self._eval_condition(stmt.condition_expr, principal, context)
            if stmt.condition_kind == "when" and not cond_val:
                return False
            if stmt.condition_kind == "unless" and cond_val:
                return False

        return True

    def evaluate(
        self,
        principal: dict[str, Any],
        action: str,
        resource: dict[str, Any],
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Evaluate Cedar statements using strict forbid-overrides-permit semantics."""
        matched_permits: list[int] = []
        matched_forbids: list[int] = []

        for stmt in self.statements:
            if self._matches_statement(stmt, principal, action, resource, context):
                if stmt.effect == "forbid":
                    matched_forbids.append(stmt.index)
                else:
                    matched_permits.append(stmt.index)

        if matched_forbids:
            return ("DENY", f"Matched Cedar forbid statement(s): {matched_forbids}")
        if matched_permits:
            return ("ALLOW", f"Matched Cedar permit statement(s): {matched_permits}")
        return ("DENY", "No Cedar permit statement matched (default deny)")


class PolicyValidator:
    """Validates Cedar policies match PolicyEngine Python fallback."""

    def __init__(
        self,
        policies_file: Path = REPO_ROOT / "policies" / "agent_authorization.cedar",
        schema_file: Path = REPO_ROOT / "policies" / "schema.cedarschema",
        engine_file: Path = REPO_ROOT / "src" / "product_value_system" / "policy" / "engine.py",
        require_cedar_cli: bool = False,
        engine: PolicyEngine | None = None,
    ) -> None:
        self.policies_file = Path(policies_file)
        self.schema_file = Path(schema_file)
        self.engine_file = Path(engine_file)
        self.require_cedar_cli = require_cedar_cli

        if not self.policies_file.exists():
            raise FileNotFoundError(f"Policies not found: {self.policies_file}")
        if not self.schema_file.exists():
            raise FileNotFoundError(f"Schema not found: {self.schema_file}")
        if not self.engine_file.exists():
            raise FileNotFoundError(f"Engine not found: {self.engine_file}")

        self.cedar_cli_available = self._detect_cedar_cli()
        if self.require_cedar_cli and not self.cedar_cli_available:
            raise RuntimeError(
                "Cedar CLI not found. Install: brew install cedar-policy/cedar/cedar-cli "
                "or cargo install cedar-policy-cli"
            )

        self._parser = CedarPolicyParser(self.policies_file.read_text(encoding="utf-8"))
        self._engine = engine or PolicyEngine(
            policies_file=self.policies_file,
            schema_file=self.schema_file,
        )

    def _detect_cedar_cli(self) -> bool:
        if not shutil.which("cedar"):
            return False
        try:
            proc = subprocess.run(
                ["cedar", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def generate_test_cases(self) -> list[TestCase]:
        """
        Generate 24 comprehensive authorization test cases covering every rule,
        boundary condition, and negative/privilege-escalation scenario.
        """
        cases: list[TestCase] = [
            # 1. Discovery agent cases
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="query",
                resource={"type": "Tool", "name": "knowledge_base"},
                context={"environment": "sandbox", "stage": "discovery", "cost_so_far": 1.0},
                description="Discovery can query knowledge_base in sandbox",
            ),
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="call",
                resource={"type": "Tool", "name": "knowledge_base"},
                context={"environment": "sandbox", "stage": "discovery", "cost_so_far": 1.0},
                description="Discovery can call knowledge_base tool in sandbox",
            ),
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="query",
                resource={"type": "Tool", "name": "knowledge_base"},
                context={"environment": "production", "stage": "discovery", "cost_so_far": 1.0},
                description="Discovery cannot query knowledge_base in production",
            ),
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="write",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "discovery", "cost_so_far": 1.0},
                description="Discovery cannot write spec_editor in sandbox",
            ),
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="execute",
                resource={"type": "Tool", "name": "sandbox"},
                context={"environment": "sandbox", "iteration_count": 1, "cost_so_far": 1.0},
                description="Discovery cannot execute code in sandbox",
            ),
            # 2. Specification agent cases
            TestCase(
                principal={"role": "specification", "name": "spec-001", "costBudget": 100.0},
                action="read",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "specification", "cost_so_far": 2.0},
                description="Specification agent can read spec_editor during specification stage",
            ),
            TestCase(
                principal={"role": "specification", "name": "spec-001", "costBudget": 100.0},
                action="write",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "specification", "cost_so_far": 2.0},
                description="Specification agent can write spec_editor during specification stage",
            ),
            TestCase(
                principal={"role": "specification", "name": "spec-001", "costBudget": 100.0},
                action="write",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "discovery", "cost_so_far": 2.0},
                description="Specification agent denied write outside specification stage",
            ),
            TestCase(
                principal={"role": "specification", "name": "spec-001", "costBudget": 100.0},
                action="execute",
                resource={"type": "Tool", "name": "sandbox"},
                context={"environment": "sandbox", "stage": "specification", "cost_so_far": 2.0},
                description="Specification agent cannot execute code in sandbox",
            ),
            # 3. Build agent cases (execution, iteration limit, production deploy)
            TestCase(
                principal={"role": "build", "name": "build-001", "costBudget": 100.0},
                action="execute",
                resource={"type": "Tool", "name": "sandbox"},
                context={"environment": "sandbox", "iteration_count": 1, "cost_so_far": 5.0},
                description="Build agent can execute in sandbox when iteration_count < 10",
            ),
            TestCase(
                principal={"role": "build", "name": "build-001", "costBudget": 100.0},
                action="execute",
                resource={"type": "Tool", "name": "sandbox"},
                context={"environment": "sandbox", "iteration_count": 10, "cost_so_far": 5.0},
                description="Build agent denied execute at iteration limit boundary (10)",
            ),
            TestCase(
                principal={"role": "build", "name": "build-001", "costBudget": 100.0},
                action="execute",
                resource={"type": "Tool", "name": "sandbox"},
                context={"environment": "production", "iteration_count": 1, "cost_so_far": 5.0},
                description="Build agent denied execute in production environment",
            ),
            TestCase(
                principal={"role": "build", "name": "build-001", "costBudget": 100.0},
                action="deploy",
                resource={"type": "Environment", "name": "production"},
                context={
                    "environment": "production",
                    "has_human_approval": False,
                    "cost_so_far": 5.0,
                },
                description="Build agent denied production deploy without human approval",
            ),
            TestCase(
                principal={"role": "build", "name": "build-001", "costBudget": 100.0},
                action="deploy",
                resource={"type": "Environment", "name": "production"},
                context={
                    "environment": "production",
                    "has_human_approval": True,
                    "cost_so_far": 5.0,
                },
                description="Build agent allowed production deploy with human approval",
            ),
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="deploy",
                resource={"type": "Environment", "name": "production"},
                context={
                    "environment": "production",
                    "has_human_approval": True,
                    "cost_so_far": 1.0,
                },
                description="Non-build agent denied production deploy even with human approval",
            ),
            # 4. Review agent read-only cases
            TestCase(
                principal={"role": "architecture_review", "name": "arch-001", "costBudget": 50.0},
                action="read",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "review", "cost_so_far": 1.0},
                description="Architecture review agent allowed read during review stage",
            ),
            TestCase(
                principal={"role": "security_review", "name": "sec-001", "costBudget": 50.0},
                action="read",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "review", "cost_so_far": 1.0},
                description="Security review agent allowed read during review stage",
            ),
            TestCase(
                principal={"role": "architecture_review", "name": "arch-001", "costBudget": 50.0},
                action="write",
                resource={"type": "Tool", "name": "spec_editor"},
                context={"environment": "sandbox", "stage": "review", "cost_so_far": 1.0},
                description="Architecture review agent denied write (read-only)",
            ),
            TestCase(
                principal={"role": "security_review", "name": "sec-001", "costBudget": 50.0},
                action="execute",
                resource={"type": "Tool", "name": "sandbox"},
                context={"environment": "sandbox", "stage": "review", "cost_so_far": 1.0},
                description="Security review agent denied execute (read-only)",
            ),
            TestCase(
                principal={"role": "security_review", "name": "sec-001", "costBudget": 50.0},
                action="deploy",
                resource={"type": "Environment", "name": "production"},
                context={
                    "environment": "production",
                    "has_human_approval": True,
                    "cost_so_far": 1.0,
                },
                description="Security review agent denied deploy (read-only)",
            ),
            # 5. Cost budget forbid cases (forbid overrides permit)
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="query",
                resource={"type": "Tool", "name": "knowledge_base"},
                context={"environment": "sandbox", "cost_so_far": 50.0},
                description="Discovery within cost budget (allowed)",
            ),
            TestCase(
                principal={"role": "discovery", "name": "discovery-001", "costBudget": 100.0},
                action="query",
                resource={"type": "Tool", "name": "knowledge_base"},
                context={"environment": "sandbox", "cost_so_far": 150.0},
                description="Discovery exceeding cost budget (forbid overrides permit)",
            ),
            # 6. External API call cases
            TestCase(
                principal={
                    "role": "build",
                    "name": "build-001",
                    "trustLevel": "high",
                    "costBudget": 100.0,
                },
                action="call",
                resource={"type": "Tool", "name": "external_api"},
                context={
                    "environment": "sandbox",
                    "has_human_approval": True,
                    "cost_so_far": 1.0,
                },
                description="High-trust agent allowed external_api call with human approval",
            ),
            TestCase(
                principal={
                    "role": "build",
                    "name": "build-001",
                    "trustLevel": "low",
                    "costBudget": 100.0,
                },
                action="call",
                resource={"type": "Tool", "name": "external_api"},
                context={
                    "environment": "sandbox",
                    "has_human_approval": True,
                    "cost_so_far": 1.0,
                },
                description="Low-trust agent denied external_api call even with approval",
            ),
            TestCase(
                principal={
                    "role": "build",
                    "name": "build-001",
                    "trustLevel": "high",
                    "costBudget": 100.0,
                },
                action="call",
                resource={"type": "Tool", "name": "external_api"},
                context={
                    "environment": "sandbox",
                    "has_human_approval": False,
                    "cost_so_far": 1.0,
                },
                description="High-trust agent denied external_api call without human approval",
            ),
        ]
        return cases

    async def evaluate_cedar(self, test_case: TestCase) -> tuple[str, str, str]:
        """
        Evaluate test case against policies/agent_authorization.cedar.

        Uses Cedar CLI if available, otherwise uses the independent CedarPolicyParser.

        Returns:
            (decision, reason, mode) tuple where decision is "ALLOW" or "DENY"
        """
        if self.cedar_cli_available:
            decision, reason = await self._evaluate_via_cedar_cli(test_case)
            return decision, reason, "cedar_cli"

        decision, reason = self._parser.evaluate(
            principal=test_case.principal,
            action=test_case.action,
            resource=test_case.resource,
            context=test_case.context,
        )
        return decision, reason, "cedar_parser"

    async def _evaluate_via_cedar_cli(self, test_case: TestCase) -> tuple[str, str]:
        """Invoke official Cedar CLI binary to evaluate a test case."""
        principal_id = str(test_case.principal.get("name", "agent-001"))
        role_name = str(test_case.principal.get("role", "unknown"))
        trust_level = str(test_case.principal.get("trustLevel", "medium"))
        cost_budget = int(float(test_case.principal.get("costBudget", 100.0)))

        res_type = str(test_case.resource.get("type", "Tool"))
        res_id = str(test_case.resource.get("name", "unknown"))

        entities = [
            {
                "uid": {"type": "ProductValueSystem::AgentRole", "id": role_name},
                "attrs": {"name": role_name, "trustLevel": trust_level},
                "parents": [],
            },
            {
                "uid": {"type": "ProductValueSystem::Agent", "id": principal_id},
                "attrs": {
                    "role": {
                        "__entity": {
                            "type": "ProductValueSystem::AgentRole",
                            "id": role_name,
                        }
                    },
                    "trustLevel": trust_level,
                    "costBudget": cost_budget,
                },
                "parents": [{"type": "ProductValueSystem::AgentRole", "id": role_name}],
            },
            {
                "uid": {"type": f"ProductValueSystem::{res_type}", "id": res_id},
                "attrs": {},
                "parents": [],
            },
        ]

        context_payload = {
            "environment": str(test_case.context.get("environment", "sandbox")),
            "has_human_approval": bool(test_case.context.get("has_human_approval", False)),
            "cost_so_far": int(float(test_case.context.get("cost_so_far", 0))),
            "iteration_count": int(test_case.context.get("iteration_count", 0)),
            "stage": str(test_case.context.get("stage", "discovery")),
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            entities_file = Path(tmp_dir) / "entities.json"
            context_file = Path(tmp_dir) / "context.json"
            entities_file.write_text(json.dumps(entities), encoding="utf-8")
            context_file.write_text(json.dumps(context_payload), encoding="utf-8")

            proc = await asyncio.create_subprocess_exec(
                "cedar",
                "authorize",
                "--policies",
                str(self.policies_file),
                "--entities",
                str(entities_file),
                "--context",
                str(context_file),
                "--principal",
                f'ProductValueSystem::Agent::"{principal_id}"',
                "--action",
                f'ProductValueSystem::Action::"{test_case.action}"',
                "--resource",
                f'ProductValueSystem::{res_type}::"{res_id}"',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode == 0 or stdout_text.upper() in {"ALLOW", "DENY"}:
                decision = "ALLOW" if stdout_text.upper().startswith("ALLOW") else "DENY"
                return decision, f"Cedar CLI decision: {stdout_text}"

            raise RuntimeError(
                f"Cedar CLI failed (exit {proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )

    def evaluate_fallback(self, test_case: TestCase) -> tuple[str, str]:
        """Evaluate test case via PolicyEngine._fallback_evaluate()."""
        request = self._engine._build_request(
            principal=test_case.principal,
            action=test_case.action,
            resource=test_case.resource,
            context=test_case.context,
        )
        decision_obj = self._engine._fallback_evaluate(request)
        decision_str = "ALLOW" if decision_obj.decision == Decision.ALLOW else "DENY"
        return decision_str, decision_obj.reason

    async def validate_all(self, verbose: bool = False) -> list[ValidationResult]:
        """Validate all generated test cases and return detailed results."""
        test_cases = self.generate_test_cases()
        results: list[ValidationResult] = []

        if verbose:
            mode = "Cedar CLI" if self.cedar_cli_available else "Cedar DSL Parser"
            print(f"Validating {len(test_cases)} policy sync test cases (mode: {mode})...")

        for idx, test_case in enumerate(test_cases, start=1):
            cedar_decision, cedar_reason, mode = await self.evaluate_cedar(test_case)
            fallback_decision, fallback_reason = self.evaluate_fallback(test_case)
            match = cedar_decision == fallback_decision

            result = ValidationResult(
                test_case=test_case,
                cedar_decision=cedar_decision,
                fallback_decision=fallback_decision,
                match=match,
                cedar_reason=cedar_reason,
                fallback_reason=fallback_reason,
                evaluator_mode=mode,
            )
            results.append(result)

            if not match:
                print(f"[MISMATCH] [{idx}/{len(test_cases)}] {test_case.description}")
                print(f"  Cedar:    {cedar_decision} ({cedar_reason})")
                print(f"  Fallback: {fallback_decision} ({fallback_reason})")
            elif verbose:
                print(
                    f"[OK] [{idx}/{len(test_cases)}] {cedar_decision:5s} | "
                    f"{test_case.description}"
                )

        return results

    def report(self, results: list[ValidationResult]) -> dict[str, Any]:
        """Generate structured JSON-serializable validation report."""
        total = len(results)
        matches = sum(1 for r in results if r.match)
        mismatches = total - matches

        mismatch_details = [
            {
                "description": r.test_case.description,
                "cedar": r.cedar_decision,
                "fallback": r.fallback_decision,
                "cedar_reason": r.cedar_reason,
                "fallback_reason": r.fallback_reason,
                "principal": r.test_case.principal,
                "action": r.test_case.action,
                "resource": r.test_case.resource,
                "context": r.test_case.context,
            }
            for r in results
            if not r.match
        ]

        return {
            "summary": {
                "total_tests": total,
                "matches": matches,
                "mismatches": mismatches,
                "pass_rate": matches / total if total > 0 else 0.0,
                "evaluator_mode": (
                    results[0].evaluator_mode if results else "unknown"
                ),
            },
            "mismatches": mismatch_details,
        }


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Validate Cedar policies match PolicyEngine Python fallback"
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose per-case output")
    parser.add_argument("--output", type=Path, help="Write JSON report to file path")
    parser.add_argument(
        "--require-cedar-cli",
        action="store_true",
        help="Fail if official Cedar CLI binary is not installed",
    )
    args = parser.parse_args()

    validator = PolicyValidator(require_cedar_cli=args.require_cedar_cli)
    results = asyncio.run(validator.validate_all(verbose=args.verbose))
    report = validator.report(results)

    print("\n" + "=" * 60)
    print("POLICY SYNC VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Evaluator mode: {report['summary']['evaluator_mode']}")
    print(f"Total tests:    {report['summary']['total_tests']}")
    print(f"Matches:        {report['summary']['matches']}")
    print(f"Mismatches:     {report['summary']['mismatches']}")
    print(f"Pass rate:      {report['summary']['pass_rate'] * 100:.1f}%")

    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report saved to: {args.output}")

    if report["summary"]["mismatches"] > 0:
        print("\nVALIDATION FAILED: Cedar policies and Python fallback are out of sync!")
        sys.exit(1)

    print("\nVALIDATION PASSED: Cedar policies and Python fallback are in sync.")
    sys.exit(0)


if __name__ == "__main__":
    main()

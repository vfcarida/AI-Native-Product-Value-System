"""
Structural and semantic drift comparator between code and specifications (EVO-017).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from product_value_system.drift.code_scanner import APIEndpoint, FunctionSignature
from product_value_system.drift.spec_scanner import SpecEndpoint, SpecFunction


class DriftSeverity(str, Enum):
    """Severity taxonomy for code-specification drift."""

    MINOR = "minor"  # Docstring formatting, default tweak
    MODERATE = "moderate"  # Optional parameter added, semantic doc mismatch
    MAJOR = "major"  # Required parameter added, type mismatch, SLA overrun
    CRITICAL = "critical"  # Breaking change (removed param/endpoint, error SLA breach)


@dataclass
class DriftFinding:
    """Single detected divergence between implementation, specification, or telemetry."""

    severity: DriftSeverity
    category: str  # "signature", "behavior", "documentation", "endpoint", "stale_doc"
    description: str
    code_location: str
    spec_location: str
    suggestion: str
    details: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable content hash used for brownfield baseline diffing and allowlisting."""
        key = f"{self.category}|{self.code_location}|{self.spec_location}|{self.description}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """Serialize drift finding to JSON/YAML dictionary."""
        return {
            "fingerprint": self.fingerprint(),
            "severity": self.severity.value,
            "category": self.category,
            "description": self.description,
            "code_location": self.code_location,
            "spec_location": self.spec_location,
            "suggestion": self.suggestion,
            "details": dict(self.details),
        }


def _normalize_type(type_str: str) -> str:
    """Normalize equivalent Python typing strings to prevent false positives."""
    cleaned = re.sub(r"\s+", "", type_str.strip())
    aliases = {
        "Dict": "dict",
        "List": "list",
        "Tuple": "tuple",
        "Set": "set",
        "FrozenSet": "frozenset",
    }
    for old, new in aliases.items():
        cleaned = re.sub(rf"\b{old}\b", new, cleaned)
    # Normalize Optional[X] -> X|None
    opt_match = re.fullmatch(r"Optional\[(.+)\]", cleaned)
    if opt_match:
        cleaned = f"{opt_match.group(1)}|None"
    return cleaned


class DriftComparator:
    """Compare extracted code signatures and endpoints with specification contracts."""

    def __init__(
        self,
        llm_callable: Callable[..., str] | None = None,
        *,
        allowlist: set[str] | None = None,
    ) -> None:
        self.llm = llm_callable
        self.allowlist: set[str] = set(allowlist or set())
        self._semantic_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _is_allowlisted(self, name: str, location: str) -> bool:
        return name in self.allowlist or location in self.allowlist

    def compare_functions(
        self,
        code_sig: FunctionSignature,
        spec_func: SpecFunction,
    ) -> list[DriftFinding]:
        """Compare code function signature against its specification contract."""
        code_loc = f"{code_sig.module}::{code_sig.qualified_name}"
        spec_loc = f"{spec_func.source_file}:L{spec_func.line_number}"
        if self._is_allowlisted(code_sig.name, code_loc):
            return []

        findings: list[DriftFinding] = []
        code_params = {p["name"]: p for p in code_sig.parameters}
        spec_params = {p["name"]: p for p in spec_func.parameters}

        # 1. Added parameters (in code, absent from spec)
        for param_name, code_param in code_params.items():
            if param_name not in spec_params:
                has_default = code_param.get("default") is not None
                severity = DriftSeverity.MODERATE if has_default else DriftSeverity.MAJOR
                default_suffix = (
                    f" = {code_param['default']}" if has_default else ""
                )
                findings.append(
                    DriftFinding(
                        severity=severity,
                        category="signature",
                        description=(
                            f"Function `{code_sig.name}()` added parameter '{param_name}' "
                            "not in specification"
                        ),
                        code_location=code_loc,
                        spec_location=spec_loc,
                        suggestion=(
                            f"Update spec at `{spec_func.source_file}` line "
                            f"{spec_func.line_number}: add `{param_name}: "
                            f"{code_param['type']}{default_suffix}`"
                        ),
                        details={
                            "parameter": param_name,
                            "type": code_param["type"],
                            "default": code_param.get("default"),
                        },
                    )
                )

        # 2. Removed parameters (in spec, absent from code)
        for param_name in spec_params:
            if param_name not in code_params:
                findings.append(
                    DriftFinding(
                        severity=DriftSeverity.CRITICAL,
                        category="signature",
                        description=(
                            f"Parameter '{param_name}' in `{code_sig.name}()` exists in "
                            "specification but was removed from code"
                        ),
                        code_location=code_loc,
                        spec_location=spec_loc,
                        suggestion=(
                            f"Remove `{param_name}` from `{spec_func.source_file}` line "
                            f"{spec_func.line_number} or restore in code"
                        ),
                        details={"parameter": param_name},
                    )
                )

        # 3. Parameter type & default comparisons
        for param_name, code_param in code_params.items():
            if param_name not in spec_params:
                continue
            spec_param = spec_params[param_name]
            code_type = _normalize_type(str(code_param["type"]))
            spec_type = _normalize_type(str(spec_param["type"]))
            if code_type != spec_type and spec_type != "Any":
                findings.append(
                    DriftFinding(
                        severity=DriftSeverity.MAJOR,
                        category="signature",
                        description=(
                            f"Parameter '{param_name}' type mismatch in `{code_sig.name}()` "
                            f"(code: {code_param['type']}, spec: {spec_param['type']})"
                        ),
                        code_location=code_loc,
                        spec_location=spec_loc,
                        suggestion=(
                            f"Update `{spec_func.source_file}` line {spec_func.line_number} "
                            f"type from `{spec_param['type']}` to `{code_param['type']}`"
                        ),
                        details={
                            "parameter": param_name,
                            "code_type": code_param["type"],
                            "spec_type": spec_param["type"],
                        },
                    )
                )
            elif (
                spec_param.get("default") is not None
                and code_param.get("default") != spec_param.get("default")
            ):
                findings.append(
                    DriftFinding(
                        severity=DriftSeverity.MINOR,
                        category="signature",
                        description=(
                            f"Parameter '{param_name}' default value changed in "
                            f"`{code_sig.name}()` (spec: {spec_param.get('default')}, "
                            f"code: {code_param.get('default')})"
                        ),
                        code_location=code_loc,
                        spec_location=spec_loc,
                        suggestion=(
                            f"Update default value for `{param_name}` at "
                            f"`{spec_func.source_file}` line {spec_func.line_number}"
                        ),
                        details={
                            "parameter": param_name,
                            "code_default": code_param.get("default"),
                            "spec_default": spec_param.get("default"),
                        },
                    )
                )

        # 4. Return type comparison
        norm_code_ret = _normalize_type(code_sig.return_type)
        norm_spec_ret = _normalize_type(spec_func.returns)
        if norm_code_ret != norm_spec_ret and norm_spec_ret != "Any":
            findings.append(
                DriftFinding(
                    severity=DriftSeverity.MAJOR,
                    category="signature",
                    description=(
                        f"Return type mismatch for `{code_sig.name}()` "
                        f"(code: {code_sig.return_type}, spec: {spec_func.returns})"
                    ),
                    code_location=code_loc,
                    spec_location=spec_loc,
                    suggestion=(
                        f"Update return type at `{spec_func.source_file}` line "
                        f"{spec_func.line_number} from `{spec_func.returns}` "
                        f"to `{code_sig.return_type}`"
                    ),
                    details={
                        "code_return": code_sig.return_type,
                        "spec_return": spec_func.returns,
                    },
                )
            )

        # 5. Optional semantic docstring comparison
        if self.llm is not None and code_sig.docstring and spec_func.description:
            semantic_drift = self._compare_descriptions(
                code_docstring=code_sig.docstring,
                spec_description=spec_func.description,
                code_location=code_loc,
                spec_location=spec_loc,
            )
            if semantic_drift is not None:
                findings.append(semantic_drift)

        return findings

    def _compare_descriptions(
        self,
        code_docstring: str,
        spec_description: str,
        *,
        code_location: str = "docstring",
        spec_location: str = "specification",
    ) -> DriftFinding | None:
        """Compare docstring and specification prose semantically using cached LLM calls."""
        if code_docstring.strip() == spec_description.strip():
            return None

        cache_key = (code_docstring.strip(), spec_description.strip())
        result = self._semantic_cache.get(cache_key)
        if result is None:
            if self.llm is None:
                return None
            prompt = (
                "Compare these two function descriptions:\n\n"
                f"Code Docstring:\n{code_docstring}\n\n"
                f"Specification:\n{spec_description}\n\n"
                "Are they semantically equivalent? If not, what is different?\n"
                'Answer strictly as JSON: {"equivalent": true/false, "difference": "explanation"}'
            )
            try:
                raw_response = self.llm(prompt, temperature=0.0)
                result = json.loads(raw_response)
                if not isinstance(result, dict):
                    return None
                self._semantic_cache[cache_key] = result
            except Exception:
                return None

        if not result.get("equivalent", True):
            return DriftFinding(
                severity=DriftSeverity.MODERATE,
                category="documentation",
                description="Semantic drift between code docstring and specification",
                code_location=code_location,
                spec_location=spec_location,
                suggestion=f"Align specification at `{spec_location}` with updated code behavior",
                details={"difference": str(result.get("difference", ""))},
            )
        return None

    def compare_endpoints(
        self,
        code_endpoint: APIEndpoint,
        spec_endpoint: SpecEndpoint,
    ) -> list[DriftFinding]:
        """Compare FastAPI route parameters against OpenAPI specification."""
        code_loc = f"{code_endpoint.method} {code_endpoint.path} ({code_endpoint.handler})"
        if self._is_allowlisted(code_endpoint.path, code_loc):
            return []

        findings: list[DriftFinding] = []
        code_params = set(code_endpoint.request_schema.keys())
        spec_params = set(spec_endpoint.request_body.get("properties", {}).keys())

        for param in sorted(code_params - spec_params):
            findings.append(
                DriftFinding(
                    severity=DriftSeverity.MAJOR,
                    category="endpoint",
                    description=(
                        f"Request parameter '{param}' added to {code_endpoint.method} "
                        f"{code_endpoint.path} but missing from OpenAPI specification"
                    ),
                    code_location=code_loc,
                    spec_location=spec_endpoint.source_file,
                    suggestion=(
                        f"Add parameter '{param}' to OpenAPI spec `{spec_endpoint.source_file}`"
                    ),
                    details={"parameter": param},
                )
            )

        for param in sorted(spec_params - code_params):
            findings.append(
                DriftFinding(
                    severity=DriftSeverity.CRITICAL,
                    category="endpoint",
                    description=(
                        f"Request parameter '{param}' in OpenAPI specification was removed "
                        f"from {code_endpoint.method} {code_endpoint.path}"
                    ),
                    code_location=code_loc,
                    spec_location=spec_endpoint.source_file,
                    suggestion=(
                        f"Remove '{param}' from `{spec_endpoint.source_file}` or restore in handler"
                    ),
                    details={"parameter": param},
                )
            )

        return findings

    def check_doc_freshness(
        self,
        code_path: str | Path,
        spec_path: str | Path,
        *,
        max_lag_seconds: float = 86400.0 * 7,
    ) -> list[DriftFinding]:
        """Flag stale documentation when code was modified significantly after its spec."""
        c_path = Path(code_path)
        s_path = Path(spec_path)
        if not c_path.exists() or not s_path.exists():
            return []
        lag = c_path.stat().st_mtime - s_path.stat().st_mtime
        if lag > max_lag_seconds:
            days = lag / 86400.0
            return [
                DriftFinding(
                    severity=DriftSeverity.MINOR,
                    category="stale_doc",
                    description=(
                        f"Documentation `{s_path.name}` is {days:.1f} days older than "
                        f"implementation `{c_path.name}`"
                    ),
                    code_location=str(code_path),
                    spec_location=str(spec_path),
                    suggestion=f"Review and refresh `{spec_path}` to confirm alignment",
                    details={"lag_days": round(days, 2)},
                )
            ]
        return []

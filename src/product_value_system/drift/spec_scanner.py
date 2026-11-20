"""
Specification scanner extracting function contracts, OpenAPI endpoints, and
behavioral SLA constraints from Markdown, OpenAPI, and YAML specs (EVO-017).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from product_value_system.drift.code_scanner import CodeScanner

_PYTHON_BLOCK_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_DEF_STUB_RE = re.compile(
    r"^(?:async\s+)?def\s+\w+\s*\([^)]*\)(?:\s*->\s*[^:]+)?:\s*$",
    re.MULTILINE,
)
_LATENCY_RANGE_RE = re.compile(
    r"(?:(?:(?P<op>[A-Za-z_][A-Za-z0-9_.-]*)\s+)?.*?)"
    r"(?:complete\s+in|under|within|latency\s*(?:<=|<|of|target:?))\s*"
    r"(?:(?P<min_val>\d+(?:\.\d+)?)\s*(?:-|to)\s*)?"
    r"(?P<max_val>\d+(?:\.\d+)?)\s*(?P<unit>ms|milliseconds?|s|sec|seconds?)\b",
    re.IGNORECASE,
)
_ERROR_RATE_RE = re.compile(
    r"error\s+rate\s*(?:<=|<|under|below)\s*(?P<val>\d+(?:\.\d+)?)\s*(?P<pct>%?)",
    re.IGNORECASE,
)


@dataclass
class SpecFunction:
    """Function signature contract extracted from Markdown specification."""

    name: str
    description: str
    parameters: list[dict[str, Any]]
    returns: str
    examples: list[str] = field(default_factory=list)
    source_file: str = ""
    line_number: int = 1
    is_async: bool = False


@dataclass
class SpecEndpoint:
    """API endpoint contract extracted from OpenAPI YAML/JSON specification."""

    path: str
    method: str
    description: str
    request_body: dict[str, Any] = field(default_factory=dict)
    response_body: dict[str, Any] = field(default_factory=dict)
    status_codes: dict[int, str] = field(default_factory=dict)
    source_file: str = ""
    line_number: int = 1


@dataclass
class BehavioralSpec:
    """Runtime behavioral SLA expectation extracted from specification docs/YAML."""

    operation: str
    max_latency_ms: float | None = None
    min_latency_ms: float | None = None
    max_error_rate: float | None = None
    source_file: str = ""
    line_number: int = 1
    raw_clause: str = ""


class SpecScanner:
    """Scan Markdown, OpenAPI, and PVS YAML specifications for API and SLA contracts."""

    def __init__(self) -> None:
        self._code_scanner = CodeScanner()

    def scan_markdown_spec(self, spec_path: str | Path) -> list[SpecFunction]:
        """Parse Markdown specification for Python function contracts in fenced code blocks."""
        path = Path(spec_path)
        content = path.read_text(encoding="utf-8")
        functions: list[SpecFunction] = []

        for match in _PYTHON_BLOCK_RE.finditer(content):
            block_code = match.group(1)
            block_start_line = content[: match.start(1)].count("\n") + 1
            tree = self._parse_code_snippet(block_code)
            if tree is None:
                continue

            for node in tree.body:
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    sig = self._code_scanner._extract_signature(node, module_path=str(spec_path))
                    functions.append(
                        SpecFunction(
                            name=sig.name,
                            description=sig.docstring or self._nearby_heading_description(
                                content, match.start()
                            ),
                            parameters=sig.parameters,
                            returns=sig.return_type,
                            examples=self._extract_examples(content, sig.name),
                            source_file=str(spec_path),
                            line_number=block_start_line + node.lineno - 1,
                            is_async=sig.is_async,
                        )
                    )

        return functions

    def _parse_code_snippet(self, snippet: str) -> ast.Module | None:
        """Parse Python code block, tolerating bodyless signature stubs in docs."""
        try:
            return ast.parse(snippet)
        except SyntaxError:
            # Handle documentation stubs like `def foo(x: int) -> bool:` without a body
            patched = _DEF_STUB_RE.sub(lambda m: f"{m.group(0)}\n    pass", snippet)
            try:
                return ast.parse(patched)
            except SyntaxError:
                # Try appending `    pass` at the end (e.g., stub with docstring only)
                try:
                    return ast.parse(snippet + "\n    pass\n")
                except SyntaxError:
                    return None

    def _nearby_heading_description(self, content: str, block_pos: int) -> str:
        """Fallback description from prose immediately preceding the code block."""
        prefix = content[max(0, block_pos - 400) : block_pos].strip()
        lines = [line.strip() for line in prefix.splitlines() if line.strip()]
        prose = [line for line in lines if not line.startswith("#") and not line.startswith("```")]
        return prose[-1] if prose else ""

    def _extract_examples(self, content: str, func_name: str) -> list[str]:
        examples: list[str] = []
        call_re = re.compile(rf"\b{re.escape(func_name)}\s*\(")
        def_re = re.compile(rf"\bdef\s+{re.escape(func_name)}\s*\(")
        for match in _PYTHON_BLOCK_RE.finditer(content):
            code = match.group(1)
            if call_re.search(code) and not def_re.search(code):
                examples.append(match.group(0))
        return examples

    def scan_openapi_spec(self, spec_path: str | Path) -> list[SpecEndpoint]:
        """Parse OpenAPI 3.x YAML or JSON file into ``SpecEndpoint`` contracts."""
        path = Path(spec_path)
        raw = path.read_text(encoding="utf-8")
        spec = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
        if not isinstance(spec, dict) or "paths" not in spec:
            return []

        endpoints: list[SpecEndpoint] = []
        for route_path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                    continue
                if not isinstance(operation, dict):
                    continue

                properties: dict[str, Any] = {}
                # Extract OpenAPI 3 requestBody properties (both shorthand and application/json)
                req_body = operation.get("requestBody", {})
                if isinstance(req_body, dict):
                    if isinstance(req_body.get("properties"), dict):
                        properties.update(req_body["properties"])
                    content = req_body.get("content", {})
                    if isinstance(content, dict):
                        for media_obj in content.values():
                            if isinstance(media_obj, dict):
                                schema = media_obj.get("schema", {})
                                if isinstance(schema, dict) and isinstance(
                                    schema.get("properties"), dict
                                ):
                                    properties.update(schema["properties"])

                # Also extract query/path parameters
                for param in operation.get("parameters", []):
                    if isinstance(param, dict) and param.get("name"):
                        param_schema = param.get("schema", {})
                        properties[str(param["name"])] = (
                            param_schema.get("type", "Any")
                            if isinstance(param_schema, dict)
                            else "Any"
                        )

                status_codes: dict[int, str] = {}
                responses = operation.get("responses", {})
                if isinstance(responses, dict):
                    for code, resp_obj in responses.items():
                        try:
                            code_int = int(code)
                        except (TypeError, ValueError):
                            continue
                        desc = resp_obj.get("description", "") if isinstance(resp_obj, dict) else ""
                        status_codes[code_int] = str(desc)

                endpoints.append(
                    SpecEndpoint(
                        path=str(route_path),
                        method=method.upper(),
                        description=str(
                            operation.get("description") or operation.get("summary", "")
                        ),
                        request_body={"properties": properties},
                        response_body=responses if isinstance(responses, dict) else {},
                        status_codes=status_codes,
                        source_file=str(spec_path),
                    )
                )
        return endpoints

    def scan_behavioral_specs(self, spec_path: str | Path) -> list[BehavioralSpec]:
        """
        Extract runtime performance/reliability SLAs from Markdown or YAML specs.

        Supports:
        - Explicit YAML ``behavioral_slas`` / ``non_functional_requirements.performance``
        - Natural-language Markdown clauses (e.g., ``initiate_transfer complete in 2-5 seconds``)
        """
        path = Path(spec_path)
        suffix = path.suffix.lower()
        specs: list[BehavioralSpec] = []

        if suffix in {".yaml", ".yml", ".json"}:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if suffix == ".json" else yaml.safe_load(raw)
            if not isinstance(data, dict):
                return []
            for entry in data.get("behavioral_slas", []):
                if isinstance(entry, dict) and entry.get("operation"):
                    specs.append(
                        BehavioralSpec(
                            operation=str(entry["operation"]),
                            max_latency_ms=(
                                float(entry["max_latency_ms"])
                                if entry.get("max_latency_ms") is not None
                                else None
                            ),
                            min_latency_ms=(
                                float(entry["min_latency_ms"])
                                if entry.get("min_latency_ms") is not None
                                else None
                            ),
                            max_error_rate=(
                                float(entry["max_error_rate"])
                                if entry.get("max_error_rate") is not None
                                else None
                            ),
                            source_file=str(spec_path),
                            raw_clause=str(entry.get("description", "")),
                        )
                    )
            return specs

        # Markdown natural-language SLA extraction
        lines = path.read_text(encoding="utf-8").splitlines()
        current_op = "*"
        for idx, line in enumerate(lines, start=1):
            heading = re.match(r"^#{1,6}\s+`?([A-Za-z_][A-Za-z0-9_.-]*)`?", line.strip())
            if heading:
                candidate = heading.group(1)
                if candidate.lower() not in {"performance", "sla", "slas", "overview", "api"}:
                    current_op = candidate
                continue

            lat_match = _LATENCY_RANGE_RE.search(line)
            err_match = _ERROR_RATE_RE.search(line)
            if not lat_match and not err_match:
                continue

            # Detect inline backticked operation name if present
            backtick_op = re.search(r"`([A-Za-z_][A-Za-z0-9_.-]+)`", line)
            op_name = backtick_op.group(1) if backtick_op else current_op

            min_ms: float | None = None
            max_ms: float | None = None
            max_err: float | None = None

            if lat_match:
                unit = lat_match.group("unit").lower()
                mult = 1000.0 if unit.startswith("s") else 1.0
                max_ms = float(lat_match.group("max_val")) * mult
                if lat_match.group("min_val") is not None:
                    min_ms = float(lat_match.group("min_val")) * mult

            if err_match:
                val = float(err_match.group("val"))
                max_err = val / 100.0 if err_match.group("pct") == "%" else val

            specs.append(
                BehavioralSpec(
                    operation=op_name,
                    max_latency_ms=max_ms,
                    min_latency_ms=min_ms,
                    max_error_rate=max_err,
                    source_file=str(spec_path),
                    line_number=idx,
                    raw_clause=line.strip(),
                )
            )
        return specs

    def scan_directory(
        self,
        directory: str | Path,
    ) -> tuple[list[SpecFunction], list[SpecEndpoint], list[BehavioralSpec]]:
        """Scan a file or directory for Markdown functions, OpenAPI endpoints, and SLAs."""
        root = Path(directory)
        files = [root] if root.is_file() else (sorted(root.rglob("*")) if root.exists() else [])

        functions: list[SpecFunction] = []
        endpoints: list[SpecEndpoint] = []
        behavioral: list[BehavioralSpec] = []

        for path in files:
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".md":
                functions.extend(self.scan_markdown_spec(path))
                behavioral.extend(self.scan_behavioral_specs(path))
            elif suffix in {".yaml", ".yml", ".json"}:
                try:
                    endpoints.extend(self.scan_openapi_spec(path))
                    behavioral.extend(self.scan_behavioral_specs(path))
                except Exception:
                    continue
        return functions, endpoints, behavioral

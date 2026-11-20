"""
Unit tests for EVO-017 CodeScanner and SpecScanner (AST, FastAPI, Markdown, OpenAPI).
"""

import time
from pathlib import Path

import pytest

from product_value_system.drift.code_scanner import CodeScanner
from product_value_system.drift.spec_scanner import SpecScanner


@pytest.mark.unit
def test_extract_simple_function_signature(tmp_path: Path) -> None:
    """Extract parameter names, type hints, return type, and docstring."""
    module = tmp_path / "payment.py"
    module.write_text(
        '''
def transfer(amount: float, destination: str) -> bool:
    """Execute payment transfer."""
    return True
''',
        encoding="utf-8",
    )
    sigs = CodeScanner().scan_module(module)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.name == "transfer"
    assert sig.is_async is False
    assert [p["name"] for p in sig.parameters] == ["amount", "destination"]
    assert sig.parameters[0]["type"] == "float"
    assert sig.parameters[1]["type"] == "str"
    assert sig.return_type == "bool"
    assert sig.docstring == "Execute payment transfer."


@pytest.mark.unit
def test_extract_async_defaults_kwonly_and_varargs(tmp_path: Path) -> None:
    """Extract async def, default values, *args, keyword-only args, and **kwargs."""
    module = tmp_path / "advanced.py"
    module.write_text(
        '''
async def execute_batch(
    items: list[dict[str, int]],
    retries: int = 3,
    *extra: str,
    strict: bool = True,
    **metadata: object,
) -> dict[str, int]:
    """Run batch operation."""
    return {}
''',
        encoding="utf-8",
    )
    sigs = CodeScanner().scan_module(module)
    assert len(sigs) == 1
    sig = sigs[0]
    assert sig.is_async is True
    by_name = {p["name"]: p for p in sig.parameters}
    assert by_name["items"]["type"] == "list[dict[str, int]]"
    assert by_name["retries"]["default"] == "3"
    assert "*extra" in by_name and by_name["*extra"]["kind"] == "vararg"
    assert by_name["strict"]["kind"] == "kwonly" and by_name["strict"]["default"] == "True"
    assert "**metadata" in by_name and by_name["**metadata"]["kind"] == "kwarg"
    assert sig.return_type == "dict[str, int]"


@pytest.mark.unit
def test_extract_class_methods_skips_self_and_cls(tmp_path: Path) -> None:
    """Class methods omit self/cls and record qualified ClassName.method names."""
    module = tmp_path / "service.py"
    module.write_text(
        '''
class PaymentService:
    @staticmethod
    def validate(amount: float) -> bool:
        return amount > 0

    def charge(self, account_id: str, amount: float) -> dict[str, str]:
        return {"id": account_id}

    @classmethod
    def from_env(cls, prefix: str = "PAY") -> "PaymentService":
        return cls()
''',
        encoding="utf-8",
    )
    sigs = CodeScanner().scan_module(module)
    by_qual = {s.qualified_name: s for s in sigs}
    assert set(by_qual.keys()) == {
        "PaymentService.validate",
        "PaymentService.charge",
        "PaymentService.from_env",
    }
    assert [p["name"] for p in by_qual["PaymentService.charge"].parameters] == [
        "account_id",
        "amount",
    ]
    assert [p["name"] for p in by_qual["PaymentService.from_env"].parameters] == ["prefix"]
    assert "staticmethod" in by_qual["PaymentService.validate"].decorators


@pytest.mark.unit
def test_scan_fastapi_routes_extracts_methods_paths_and_schemas(tmp_path: Path) -> None:
    """Extract FastAPI @app.get/@router.post routes for sync and async handlers."""
    module = tmp_path / "routes.py"
    module.write_text(
        '''
@app.post("/transfers")
async def create_transfer(amount: float, destination: str, request: object) -> dict[str, str]:
    return {"status": "ok"}

@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str) -> dict[str, str]:
    return {"id": transfer_id}
''',
        encoding="utf-8",
    )
    endpoints = CodeScanner().scan_fastapi_routes(module)
    assert len(endpoints) == 2
    by_key = {(ep.method, ep.path): ep for ep in endpoints}
    assert ("POST", "/transfers") in by_key
    assert set(by_key[("POST", "/transfers")].request_schema.keys()) == {"amount", "destination"}
    assert ("GET", "/transfers/{transfer_id}") in by_key
    assert by_key[("GET", "/transfers/{transfer_id}")].response_schema["type"] == "dict[str, str]"


@pytest.mark.unit
def test_code_scanner_performance_under_500ms_for_50_functions(tmp_path: Path) -> None:
    """Scanning a 50-function module completes well under the 500ms acceptance criterion."""
    lines = [
        f'def fn_{i}(x: int, y: str = "v{i}", meta: dict[str, int] | None = None) -> bool:\n'
        f'    """Function {i}."""\n'
        f"    return True\n"
        for i in range(50)
    ]
    module = tmp_path / "large_module.py"
    module.write_text("\n".join(lines), encoding="utf-8")

    scanner = CodeScanner()
    start = time.perf_counter()
    sigs = scanner.scan_module(module)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert len(sigs) == 50
    assert elapsed_ms < 500.0, f"50-function scan took {elapsed_ms:.1f}ms (>500ms limit)"


@pytest.mark.unit
def test_spec_scanner_markdown_handles_stubs_multiline_and_generics(tmp_path: Path) -> None:
    """SpecScanner extracts bodyless stubs, multiline args, generic types, and line numbers."""
    spec = tmp_path / "api.md"
    spec.write_text(
        """# Transfer API

```python
async def initiate_transfer(
    amount: float,
    metadata: dict[str, str],
    require_biometric: bool = False,
) -> dict[str, object]:
    \"\"\"Initiate a payment transfer.\"\"\"
```

Usage example:
```python
result = await initiate_transfer(100.0, {"currency": "USD"})
```
""",
        encoding="utf-8",
    )
    funcs = SpecScanner().scan_markdown_spec(spec)
    assert len(funcs) == 1
    f = funcs[0]
    assert f.name == "initiate_transfer"
    assert f.is_async is True
    assert [p["name"] for p in f.parameters] == ["amount", "metadata", "require_biometric"]
    assert f.parameters[1]["type"] == "dict[str, str]"
    assert f.parameters[2]["default"] == "False"
    assert f.returns == "dict[str, object]"
    assert f.line_number == 4
    assert len(f.examples) == 1


@pytest.mark.unit
def test_spec_scanner_openapi_extracts_request_body_and_query_params(tmp_path: Path) -> None:
    """SpecScanner extracts OpenAPI 3.x requestBody properties and query/path parameters."""
    openapi = tmp_path / "openapi.yaml"
    openapi.write_text(
        """openapi: "3.0.3"
paths:
  /transfers:
    post:
      summary: Create transfer
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                amount: {type: number}
                destination: {type: string}
      responses:
        "200":
          description: Created
        "400":
          description: Invalid request
""",
        encoding="utf-8",
    )
    endpoints = SpecScanner().scan_openapi_spec(openapi)
    assert len(endpoints) == 1
    ep = endpoints[0]
    assert ep.method == "POST"
    assert ep.path == "/transfers"
    assert set(ep.request_body["properties"].keys()) == {"amount", "destination"}
    assert 200 in ep.status_codes and 400 in ep.status_codes


@pytest.mark.unit
def test_spec_scanner_extracts_markdown_and_yaml_behavioral_slas(tmp_path: Path) -> None:
    """Extract latency ranges and error rate SLAs from Markdown and YAML specs."""
    perf_md = tmp_path / "performance.md"
    perf_md.write_text(
        """## initiate_transfer
`initiate_transfer` should complete in 2-5 seconds under normal load.
Error rate < 1% across rolling 1h windows.
""",
        encoding="utf-8",
    )
    md_slas = SpecScanner().scan_behavioral_specs(perf_md)
    assert len(md_slas) == 2
    assert md_slas[0].operation == "initiate_transfer"
    assert md_slas[0].min_latency_ms == pytest.approx(2000.0)
    assert md_slas[0].max_latency_ms == pytest.approx(5000.0)
    assert md_slas[1].max_error_rate == pytest.approx(0.01)

    perf_yaml = tmp_path / "sla.yaml"
    perf_yaml.write_text(
        """behavioral_slas:
  - operation: score_risk
    max_latency_ms: 500
    max_error_rate: 0.005
""",
        encoding="utf-8",
    )
    yaml_slas = SpecScanner().scan_behavioral_specs(perf_yaml)
    assert len(yaml_slas) == 1
    assert yaml_slas[0].max_latency_ms == pytest.approx(500.0)
    assert yaml_slas[0].max_error_rate == pytest.approx(0.005)

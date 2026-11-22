"""Multi-language TypeScript & Go AST drift detection tests (v1.0)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from product_value_system.drift import CodeScanner, GoScanner, TypeScriptScanner, scan_drift


@pytest.mark.unit
def test_typescript_scanner_extracts_functions_arrows_classes_and_routes() -> None:
    """TypeScriptScanner handles generics, nested types, JSDoc, classes, Express, and NestJS."""
    ts_code = """
// function commentedOut(x: number): void {}
const fakeStr = "export function insideString(a: string) {}";

/**
 * Create a new payment intent with idempotency support.
 */
export async function createPaymentIntent<T>(
  amount: number,
  metadata: Record<string, Array<T>>,
  currency: string = "USD",
  note?: string,
  ...tags: string[]
): Promise<PaymentReceipt> {
  return {} as PaymentReceipt;
}

export const formatMoney = (cents: number, locale: string = "en-US"): string => {
  return `${cents}`;
};

@Controller("v1/orders")
export class OrderController {
  /** Fetch a single order by ID. */
  @Get(":id")
  async getOrder(id: string): Promise<Order> {
    return {} as Order;
  }

  private helperSecret(flag: boolean): void {
    if (flag) {
      return;
    }
  }
}

router.post("/v1/webhooks/stripe", handleStripeWebhook);
app.delete('/v1/orders/:id', deleteOrder);
"""
    scanner = TypeScriptScanner()
    sigs = scanner.scan_source(ts_code, module_path="orders.ts")
    by_name = {s.name: s for s in sigs}

    # Comments and string literals must NEVER produce false functions
    assert "commentedOut" not in by_name
    assert "insideString" not in by_name

    # Function with nested generics, default, optional, and rest params
    cp = by_name["createPaymentIntent"]
    assert cp.language == "typescript"
    assert cp.is_async is True
    assert cp.return_type == "Promise<PaymentReceipt>"
    assert "idempotency support" in cp.docstring
    assert [p["name"] for p in cp.parameters] == ["amount", "metadata", "currency", "note", "*tags"]
    assert cp.parameters[1]["type"] == "Record<string, Array<T>>"
    assert cp.parameters[2]["default"] == '"USD"'
    assert cp.parameters[3]["default"] == "undefined"
    assert cp.parameters[4]["kind"] == "vararg"

    # Arrow function
    fm = by_name["formatMoney"]
    assert fm.return_type == "string"
    assert [p["name"] for p in fm.parameters] == ["cents", "locale"]

    # Class method with enclosing class_name
    go_method = by_name["getOrder"]
    assert go_method.class_name == "OrderController"
    assert go_method.qualified_name == "OrderController.getOrder"
    assert go_method.is_async is True

    # exclude_private filters TypeScript `private` methods
    public_only = {s.name for s in scanner.scan_source(ts_code, exclude_private=True)}
    assert "helperSecret" not in public_only
    assert "getOrder" in public_only

    # Express + NestJS endpoints
    endpoints = scanner.scan_endpoints_source(ts_code, module_path="orders.ts")
    ep_pairs = {(ep.method, ep.path, ep.handler) for ep in endpoints}
    assert ("GET", "/v1/orders/:id", "getOrder") in ep_pairs
    assert ("POST", "/v1/webhooks/stripe", "handleStripeWebhook") in ep_pairs
    assert ("DELETE", "/v1/orders/:id", "deleteOrder") in ep_pairs


@pytest.mark.unit
def test_go_scanner_extracts_functions_receivers_shared_types_and_routes() -> None:
    """GoScanner handles grouped param types, variadics, receivers, GoDoc, net/http, and Gin."""
    go_code = """package billing

import (
    "context"
    "net/http"
)

// func CommentedOut(x int) error { return nil }
const raw = `func InsideRawString(y string) {}`

// ProcessPayment settles a transfer across accounts.
// Returns a receipt or an error.
func ProcessPayment(
    ctx context.Context,
    fromAccount, toAccount string,
    amount float64,
    opts ...Option,
) (Receipt, error) {
    return Receipt{}, nil
}

// Refund reverses an existing transaction.
func (s *PaymentService) Refund(txID string) error {
    return nil
}

func internalHelper(v int) int {
    return v + 1
}

func RegisterRoutes(mux *http.ServeMux, r *gin.Engine) {
    mux.HandleFunc("POST /api/v1/payments", HandleCreatePayment)
    mux.HandleFunc("/healthz", HandleHealth)
    r.GET("/api/v1/payments/:id", GetPayment)
    r.Delete("/api/v1/payments/:id", CancelPayment)
}
"""
    scanner = GoScanner()
    sigs = scanner.scan_source(go_code, module_path="billing.go")
    by_name = {s.name: s for s in sigs}

    assert "CommentedOut" not in by_name
    assert "InsideRawString" not in by_name

    pp = by_name["ProcessPayment"]
    assert pp.language == "go"
    assert pp.return_type == "(Receipt, error)"
    assert "settles a transfer" in pp.docstring
    # Shared-type group `fromAccount, toAccount string` expands both parameters to `string`!
    assert [(p["name"], p["type"]) for p in pp.parameters] == [
        ("ctx", "context.Context"),
        ("fromAccount", "string"),
        ("toAccount", "string"),
        ("amount", "float64"),
        ("*opts", "...Option"),
    ]
    assert pp.parameters[-1]["kind"] == "vararg"

    # Receiver method binds class_name
    refund = by_name["Refund"]
    assert refund.class_name == "PaymentService"
    assert refund.qualified_name == "PaymentService.Refund"
    assert refund.return_type == "error"

    # exclude_private excludes lowercase unexported Go functions
    exported_only = {s.name for s in scanner.scan_source(go_code, exclude_private=True)}
    assert "internalHelper" not in exported_only
    assert "ProcessPayment" in exported_only

    # net/http (including Go 1.22 method prefix) + Gin/Chi routes
    endpoints = scanner.scan_endpoints_source(go_code, module_path="billing.go")
    ep_pairs = {(ep.method, ep.path, ep.handler) for ep in endpoints}
    assert ("POST", "/api/v1/payments", "HandleCreatePayment") in ep_pairs
    assert ("GET", "/healthz", "HandleHealth") in ep_pairs
    assert ("GET", "/api/v1/payments/:id", "GetPayment") in ep_pairs
    assert ("DELETE", "/api/v1/payments/:id", "CancelPayment") in ep_pairs


@pytest.mark.unit
def test_polyglot_e2e_scan_drift_across_typescript_and_go(tmp_path: Path) -> None:
    """scan_drift detects real signature & endpoint drift across TypeScript and Go codebases."""
    code_dir = tmp_path / "services"
    (code_dir / "ts-api").mkdir(parents=True)
    (code_dir / "go-worker").mkdir(parents=True)
    (code_dir / "ts-api" / "node_modules" / "pkg").mkdir(parents=True)
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()

    # Ignored node_modules file should never be scanned
    (code_dir / "ts-api" / "node_modules" / "pkg" / "index.ts").write_text(
        "export function shouldBeIgnored(): void {}\n", encoding="utf-8"
    )

    # Real TypeScript service (drifted param name vs spec: `currency` dropped!)
    (code_dir / "ts-api" / "checkout.ts").write_text(
        """
export async function createCheckout(orderId: string, amount: number): Promise<string> {
  return orderId;
}
app.post("/api/checkout", createCheckout);
""",
        encoding="utf-8",
    )

    # Real Go service (matches ProcessRefund, missing DeletedEndpoint)
    (code_dir / "go-worker" / "refund.go").write_text(
        """package worker

func ProcessRefund(txID string, amount float64) error {
    return nil
}

func Routes(r *Router) {
    r.POST("/api/refunds", HandleRefund)
}
""",
        encoding="utf-8",
    )

    # Markdown spec documenting TS + Go contracts
    (specs_dir / "contracts.md").write_text(
        """# Polyglot Service Contracts

```typescript
export async function createCheckout(
  orderId: string,
  amount: number,
  currency: string,
): Promise<string>
```

```go
func ProcessRefund(txID string, amount float64) error
```
""",
        encoding="utf-8",
    )

    # OpenAPI spec with 2 existing endpoints and 1 removed endpoint
    (specs_dir / "openapi.yaml").write_text(
        """openapi: 3.0.3
info:
  title: Polyglot API
  version: 1.0.0
paths:
  /api/checkout:
    post:
      summary: Create checkout
  /api/refunds:
    post:
      summary: Process refund
  /api/legacy-charge:
    delete:
      summary: Removed legacy endpoint
""",
        encoding="utf-8",
    )

    scanner = CodeScanner()
    all_funcs = scanner.scan_directory(code_dir)
    assert "shouldBeIgnored" not in {f.name for f in all_funcs}
    assert {"createCheckout", "ProcessRefund"}.issubset({f.name for f in all_funcs})

    result = scan_drift(code_paths=[code_dir], spec_paths=[specs_dir])
    assert result.spec_functions_count == 2
    assert result.spec_endpoints_count == 3

    # Detect missing `currency` parameter on TS `createCheckout` & removed `/api/legacy-charge`
    descriptions = [f.description for f in result.findings]
    assert any("currency" in d and "createCheckout" in d for d in descriptions)
    assert any("/api/legacy-charge" in d for d in descriptions)
    # Matching Go `ProcessRefund` and existing `/api/checkout`, `/api/refunds` have zero drift
    assert not any("ProcessRefund" in d for d in descriptions)
    assert not any("/api/checkout" in d for d in descriptions)
    assert not any("/api/refunds" in d for d in descriptions)


@pytest.mark.unit
def test_multilang_scanners_stay_linear_on_large_adversarial_inputs() -> None:
    """50KB adversarial TS and Go inputs must scan in linear time (< 150ms, zero ReDoS)."""
    ts_payload = "const s = '" + "a" * 25000 + "';\n" + "// " + "b" * 25000 + "\n"
    go_payload = "package p\nvar s = `" + "x" * 25000 + "`\n// " + "y" * 25000 + "\n"

    t0 = time.perf_counter()
    assert TypeScriptScanner().scan_source(ts_payload) == []
    assert TypeScriptScanner().scan_endpoints_source(ts_payload) == []
    assert GoScanner().scan_source(go_payload) == []
    assert GoScanner().scan_endpoints_source(go_payload) == []
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 150.0, f"Multi-lang scanner too slow on 50KB input: {elapsed_ms:.2f}ms"

"""
Multi-language structural AST/lexer scanners for TypeScript/JavaScript and Go (v1.0).

Provides comment/string-aware, balanced-delimiter, strictly linear-time O(n) extraction of
function/method signatures and HTTP API endpoints without requiring native C/tree-sitter
toolchains, enabling seamless spec-to-code drift detection across polyglot codebases.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from product_value_system.drift.code_scanner import APIEndpoint, FunctionSignature

TS_EXTENSIONS: frozenset[str] = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs"})
GO_EXTENSIONS: frozenset[str] = frozenset({".go"})

_TS_KEYWORDS_NOT_METHODS: frozenset[str] = frozenset(
    {
        "if",
        "else",
        "for",
        "while",
        "do",
        "switch",
        "case",
        "catch",
        "try",
        "finally",
        "return",
        "throw",
        "new",
        "typeof",
        "delete",
        "void",
        "yield",
        "await",
        "super",
        "import",
        "export",
        "class",
        "interface",
        "type",
        "enum",
        "function",
        "const",
        "let",
        "var",
        "constructor",
    }
)

_HTTP_METHODS_LOWER: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "options", "head"}
)


def _mask_comments_and_strings(source: str, *, allow_backtick_strings: bool = True) -> str:
    """Return a same-length string with comments and string bodies masked to spaces.

    Preserves all ``\\n`` characters so character offsets and line numbers remain exact,
    preventing keywords or braces inside strings/comments from causing false matches.
    """
    out: list[str] = []
    n = len(source)
    i = 0
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        # Line comment // ...
        if ch == "/" and nxt == "/":
            out.append("  ")
            i += 2
            while i < n and source[i] != "\n":
                out.append(" ")
                i += 1
            continue

        # Block comment /* ... */
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            while i < n:
                if source[i] == "*" and i + 1 < n and source[i + 1] == "/":
                    out.append("  ")
                    i += 2
                    break
                out.append("\n" if source[i] == "\n" else " ")
                i += 1
            continue

        # Quoted strings ('...', "...", and optionally `...`)
        if ch in {"'", '"'} or (allow_backtick_strings and ch == "`"):
            quote = ch
            out.append(quote)
            i += 1
            while i < n:
                c = source[i]
                if c == "\\" and quote != "`" and i + 1 < n:
                    out.append(" ")
                    out.append("\n" if source[i + 1] == "\n" else " ")
                    i += 2
                    continue
                if c == quote:
                    out.append(quote)
                    i += 1
                    break
                out.append("\n" if c == "\n" else " ")
                i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _find_matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Find matching closing delimiter starting at ``start`` (returns -1 if unmatched)."""
    if start >= len(text) or text[start] != open_ch:
        return -1
    depth = 0
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    """Split ``text`` by ``sep`` only at delimiter nesting depth 0."""
    parts: list[str] = []
    buf: list[str] = []
    paren = bracket = brace = angle = 0

    for ch in text:
        if ch == "(":
            paren += 1
        elif ch == ")" and paren > 0:
            paren -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]" and bracket > 0:
            bracket -= 1
        elif ch == "{":
            brace += 1
        elif ch == "}" and brace > 0:
            brace -= 1
        elif ch == "<":
            angle += 1
        elif ch == ">" and angle > 0:
            angle -= 1

        if ch == sep and paren == 0 and bracket == 0 and brace == 0 and angle == 0:
            item = "".join(buf).strip()
            if item:
                parts.append(item)
            buf = []
        else:
            buf.append(ch)

    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _extract_leading_doc(lines: list[str], line_number: int) -> str:
    """Extract contiguous ``//`` GoDoc or ``/** ... */`` JSDoc directly above ``line_number``."""
    idx = line_number - 2  # 0-based line immediately above declaration
    while idx >= 0 and not lines[idx].strip():
        idx -= 1
    if idx < 0:
        return ""

    # Single-line // comments (GoDoc or TS comments)
    if lines[idx].strip().startswith("//"):
        doc_lines: list[str] = []
        while idx >= 0 and lines[idx].strip().startswith("//"):
            doc_lines.append(lines[idx].strip().lstrip("/").strip())
            idx -= 1
        doc_lines.reverse()
        return "\n".join(doc_lines).strip()

    # Block comment /** ... */ or /* ... */
    if lines[idx].strip().endswith("*/"):
        block: list[str] = []
        while idx >= 0:
            stripped = lines[idx].strip()
            block.append(stripped)
            if "/*" in stripped:
                break
            idx -= 1
        block.reverse()
        cleaned = [
            ln.removeprefix("/**").removeprefix("/*").removesuffix("*/").lstrip("*").strip()
            for ln in block
        ]
        return "\n".join(ln for ln in cleaned if ln).strip()

    return ""


def _join_route_path(prefix: str, subpath: str) -> str:
    p1 = prefix.strip("/")
    p2 = subpath.strip("/")
    if p1 and p2:
        return f"/{p1}/{p2}"
    if p1:
        return f"/{p1}"
    if p2:
        return f"/{p2}"
    return "/"


# ---------------------------------------------------------------------------
# TypeScript / JavaScript Scanner
# ---------------------------------------------------------------------------

_TS_CLASS_RE = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_TS_FUNC_RE = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?P<async>async\s+)?function\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
)
_TS_ARROW_RE = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=\n]+?)?\s*=\s*(?P<async>async\s+)?"
)
_TS_METHOD_RE = re.compile(
    r"^[ \t]*(?:(?P<vis>public|private|protected)\s+)?"
    r"(?:static\s+)?(?:override\s+)?(?:readonly\s+)?"
    r"(?P<async>async\s+)?(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>\n]*>)?\s*\(",
    re.MULTILINE,
)
_TS_EXPRESS_ROUTE_RE = re.compile(
    r"\b[A-Za-z_$][A-Za-z0-9_$]*\s*\.\s*(?P<method>get|post|put|delete|patch|options|head)\s*\(\s*"
    r"(?P<quote>['\"`])(?P<path>[^'\"`\n]+)(?P=quote)"
    r"(?:\s*,\s*(?:async\s+)?(?P<handler>[A-Za-z_$][A-Za-z0-9_$.]*))?"
)
_TS_NEST_CONTROLLER_RE = re.compile(
    r"@Controller\s*\(\s*(?:(?P<quote>['\"])(?P<prefix>[^'\"\n]*)(?P=quote))?\s*\)"
)
_TS_NEST_ROUTE_RE = re.compile(
    r"@(?P<method>Get|Post|Put|Delete|Patch|Options|Head)\s*\(\s*"
    r"(?:(?P<quote>['\"])(?P<path>[^'\"\n]*)(?P=quote))?\s*\)"
)


class TypeScriptScanner:
    """Structural lexer/parser extracting TypeScript/JavaScript functions & HTTP endpoints."""

    def scan_source(
        self,
        source: str,
        module_path: str = "<typescript>",
        *,
        exclude_private: bool = False,
    ) -> list[FunctionSignature]:
        """Extract function, arrow-function, and class-method signatures from TS/JS source."""
        masked = _mask_comments_and_strings(source, allow_backtick_strings=True)
        lines = source.splitlines()
        signatures: list[FunctionSignature] = []
        seen_keys: set[tuple[str | None, str, int]] = set()

        # Map class body spans so methods attach to their enclosing class_name
        class_spans = self._find_class_spans(masked)

        # 1. Named `function foo(...)` declarations
        for m in _TS_FUNC_RE.finditer(masked):
            name = m.group("name")
            if exclude_private and name.startswith("_"):
                continue
            sig = self._parse_ts_callable_after(
                source=source,
                masked=masked,
                lines=lines,
                name=name,
                scan_from=m.end(),
                decl_start=m.start(),
                is_async=bool(m.group("async")),
                module_path=module_path,
                class_name=self._class_at(class_spans, m.start()),
            )
            if sig is not None:
                key = (sig.class_name, sig.name, sig.line_number)
                if key not in seen_keys:
                    seen_keys.add(key)
                    signatures.append(sig)

        # 2. Arrow function bindings: `export const foo = async (a: string): Promise<void> =>`
        for m in _TS_ARROW_RE.finditer(masked):
            name = m.group("name")
            if exclude_private and name.startswith("_"):
                continue
            pos = m.end()
            while pos < len(masked) and masked[pos].isspace():
                pos += 1
            # Optional generic type parameter list `<T>`
            if pos < len(masked) and masked[pos] == "<":
                close_gen = _find_matching(masked, pos, "<", ">")
                if close_gen == -1:
                    continue
                pos = close_gen + 1
                while pos < len(masked) and masked[pos].isspace():
                    pos += 1
            if pos >= len(masked) or masked[pos] != "(":
                continue
            close_paren = _find_matching(masked, pos, "(", ")")
            if close_paren == -1:
                continue
            # Confirm `=>` follows optional `: ReturnType`
            ret_str, arrow_pos = self._extract_ts_return_before_token(
                source, masked, close_paren + 1, stop_tokens=("=>",)
            )
            if arrow_pos == -1:
                continue
            params = self._parse_ts_params(source[pos + 1 : close_paren])
            line_no = _line_of(source, m.start())
            sig = FunctionSignature(
                name=name,
                module=module_path,
                parameters=params,
                return_type=ret_str or "Any",
                docstring=_extract_leading_doc(lines, line_no),
                is_async=bool(m.group("async")),
                line_number=line_no,
                class_name=self._class_at(class_spans, m.start()),
                language="typescript",
            )
            key = (sig.class_name, sig.name, sig.line_number)
            if key not in seen_keys:
                seen_keys.add(key)
                signatures.append(sig)

        # 3. Class methods inside each class body
        for cls_name, body_start, body_end in class_spans:
            cls_masked = masked[body_start:body_end]
            for m in _TS_METHOD_RE.finditer(cls_masked):
                name = m.group("name")
                vis = m.group("vis") or ""
                if name in _TS_KEYWORDS_NOT_METHODS:
                    continue
                if exclude_private and (vis == "private" or name.startswith("_")):
                    continue
                abs_start = body_start + m.start()
                paren_pos = body_start + m.end() - 1
                # Only accept methods at top-level of the class body (brace depth 0 inside class)
                if self._brace_depth(masked, body_start + 1, abs_start) != 0:
                    continue
                close_paren = _find_matching(masked, paren_pos, "(", ")")
                if close_paren == -1:
                    continue
                ret_str, body_brace = self._extract_ts_return_before_token(
                    source, masked, close_paren + 1, stop_tokens=("{", ";")
                )
                if body_brace == -1:
                    continue
                params = self._parse_ts_params(source[paren_pos + 1 : close_paren])
                line_no = _line_of(source, abs_start)
                sig = FunctionSignature(
                    name=name,
                    module=module_path,
                    parameters=params,
                    return_type=ret_str or "Any",
                    docstring=_extract_leading_doc(lines, line_no),
                    is_async=bool(m.group("async")),
                    line_number=line_no,
                    class_name=cls_name,
                    language="typescript",
                )
                key = (sig.class_name, sig.name, sig.line_number)
                if key not in seen_keys:
                    seen_keys.add(key)
                    signatures.append(sig)

        signatures.sort(key=lambda s: s.line_number)
        return signatures

    def scan_file(
        self,
        file_path: str | Path,
        *,
        exclude_private: bool = False,
    ) -> list[FunctionSignature]:
        """Scan a TypeScript or JavaScript file for function/method signatures."""
        path = Path(file_path)
        source = path.read_text(encoding="utf-8", errors="ignore")
        return self.scan_source(source, module_path=str(file_path), exclude_private=exclude_private)

    def scan_endpoints_source(
        self,
        source: str,
        module_path: str = "<typescript>",
    ) -> list[APIEndpoint]:
        """Extract Express, Fastify, Hono, and NestJS HTTP endpoints from TS/JS source."""
        masked = _mask_comments_and_strings(source, allow_backtick_strings=True)
        endpoints: list[APIEndpoint] = []

        # 1. Express / Fastify / Hono route calls: app.get('/path', handler)
        for m in _TS_EXPRESS_ROUTE_RE.finditer(source):
            # Ignore matches located inside masked comments
            if masked[m.start() : m.start() + 3].strip() == "":
                continue
            method = m.group("method").upper()
            raw_path = m.group("path").strip()
            handler = m.group("handler") or "anonymous"
            endpoints.append(
                APIEndpoint(
                    path=raw_path if raw_path.startswith("/") else f"/{raw_path}",
                    method=method,
                    handler=handler,
                    request_schema={},
                    response_schema={"type": "Any"},
                    module=module_path,
                    line_number=_line_of(source, m.start()),
                    language="typescript",
                )
            )

        # 2. NestJS @Controller('prefix') + @Get('sub') method decorators
        class_spans = self._find_class_spans(masked)
        controller_prefixes: dict[str, str] = {}
        for cls_name, body_start, _ in class_spans:
            lookback = source[max(0, body_start - 300) : body_start]
            ctrl_match = _TS_NEST_CONTROLLER_RE.search(lookback)
            if ctrl_match:
                controller_prefixes[cls_name] = ctrl_match.group("prefix") or ""

        for m in _TS_NEST_ROUTE_RE.finditer(source):
            if masked[m.start() : m.start() + 1] != "@":
                continue
            method = m.group("method").upper()
            subpath = m.group("path") or ""
            enclosing_cls = self._class_at(class_spans, m.start())
            prefix = controller_prefixes.get(enclosing_cls or "", "")
            full_path = _join_route_path(prefix, subpath)

            # Find handler method name immediately following decorator(s)
            after = masked[m.end() : m.end() + 250]
            handler_match = re.search(
                r"(?:(?:public|private|protected|async|static)\s+)*"
                r"([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:<[^>\n]*>)?\s*\(",
                after,
            )
            handler_name = handler_match.group(1) if handler_match else "handler"
            endpoints.append(
                APIEndpoint(
                    path=full_path,
                    method=method,
                    handler=handler_name,
                    request_schema={},
                    response_schema={"type": "Any"},
                    module=module_path,
                    line_number=_line_of(source, m.start()),
                    language="typescript",
                )
            )

        endpoints.sort(key=lambda ep: ep.line_number)
        return endpoints

    def scan_endpoints_file(self, file_path: str | Path) -> list[APIEndpoint]:
        """Scan a TypeScript or JavaScript file for HTTP API endpoints."""
        path = Path(file_path)
        source = path.read_text(encoding="utf-8", errors="ignore")
        return self.scan_endpoints_source(source, module_path=str(file_path))

    def _find_class_spans(self, masked: str) -> list[tuple[str, int, int]]:
        spans: list[tuple[str, int, int]] = []
        for m in _TS_CLASS_RE.finditer(masked):
            brace_open = masked.find("{", m.end())
            if brace_open == -1:
                continue
            brace_close = _find_matching(masked, brace_open, "{", "}")
            if brace_close != -1:
                spans.append((m.group(1), brace_open, brace_close))
        return spans

    @staticmethod
    def _class_at(spans: list[tuple[str, int, int]], pos: int) -> str | None:
        for cls_name, start, end in spans:
            if start < pos < end:
                return cls_name
        return None

    @staticmethod
    def _brace_depth(masked: str, start: int, end: int) -> int:
        depth = 0
        for idx in range(start, end):
            ch = masked[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
        return depth

    def _parse_ts_callable_after(
        self,
        *,
        source: str,
        masked: str,
        lines: list[str],
        name: str,
        scan_from: int,
        decl_start: int,
        is_async: bool,
        module_path: str,
        class_name: str | None,
    ) -> FunctionSignature | None:
        pos = scan_from
        while pos < len(masked) and masked[pos].isspace():
            pos += 1
        if pos < len(masked) and masked[pos] == "<":
            close_gen = _find_matching(masked, pos, "<", ">")
            if close_gen == -1:
                return None
            pos = close_gen + 1
            while pos < len(masked) and masked[pos].isspace():
                pos += 1
        if pos >= len(masked) or masked[pos] != "(":
            return None
        close_paren = _find_matching(masked, pos, "(", ")")
        if close_paren == -1:
            return None

        ret_str, term_pos = self._extract_ts_return_before_token(
            source, masked, close_paren + 1, stop_tokens=("{", ";", "\n")
        )
        if term_pos == -1:
            return None
        params = self._parse_ts_params(source[pos + 1 : close_paren])
        line_no = _line_of(source, decl_start)
        return FunctionSignature(
            name=name,
            module=module_path,
            parameters=params,
            return_type=ret_str or "Any",
            docstring=_extract_leading_doc(lines, line_no),
            is_async=is_async,
            line_number=line_no,
            class_name=class_name,
            language="typescript",
        )

    @staticmethod
    def _extract_ts_return_before_token(
        source: str,
        masked: str,
        start: int,
        *,
        stop_tokens: tuple[str, ...],
    ) -> tuple[str, int]:
        pos = start
        n = len(masked)
        while pos < n and masked[pos] in " \t\r\n":
            pos += 1

        if pos < n and masked[pos] == ":":
            ret_start = pos + 1
            i = ret_start
            angle = paren = bracket = brace = 0
            while i < n:
                if angle == 0 and paren == 0 and bracket == 0 and brace == 0:
                    for tok in stop_tokens:
                        if masked.startswith(tok, i):
                            return source[ret_start:i].strip(), i
                ch = masked[i]
                if ch == "<":
                    angle += 1
                elif ch == ">" and angle > 0:
                    angle -= 1
                elif ch == "(":
                    paren += 1
                elif ch == ")" and paren > 0:
                    paren -= 1
                elif ch == "[":
                    bracket += 1
                elif ch == "]" and bracket > 0:
                    bracket -= 1
                elif ch == "{":
                    brace += 1
                elif ch == "}" and brace > 0:
                    brace -= 1
                i += 1
            return source[ret_start:].strip(), n

        for tok in stop_tokens:
            if masked.startswith(tok, pos):
                return "Any", pos
        return "Any", -1

    @staticmethod
    def _parse_ts_params(raw_params: str) -> list[dict[str, Any]]:
        params: list[dict[str, Any]] = []
        for raw in _split_top_level(raw_params, ","):
            # Strip TypeScript parameter decorators & visibility modifiers
            cleaned = re.sub(
                r"^(?:@\w+(?:\([^)]*\))?\s+|(?:public|private|protected|readonly)\s+)+",
                "",
                raw.strip(),
            ).strip()
            if not cleaned:
                continue

            is_rest = cleaned.startswith("...")
            if is_rest:
                cleaned = cleaned[3:].strip()

            # Split default value `=` at top level
            eq_parts = _split_top_level(cleaned, "=")
            lhs = eq_parts[0].strip()
            default_val: str | None = eq_parts[1].strip() if len(eq_parts) > 1 else None

            # Split type annotation `:` at top level
            colon_parts = _split_top_level(lhs, ":")
            raw_name = colon_parts[0].strip()
            type_ann = colon_parts[1].strip() if len(colon_parts) > 1 else "Any"

            is_optional = raw_name.endswith("?")
            name = raw_name.rstrip("?").strip()
            if not name:
                continue
            if is_optional and default_val is None:
                default_val = "undefined"

            params.append(
                {
                    "name": f"*{name}" if is_rest else name,
                    "type": type_ann,
                    "default": default_val,
                    "kind": "vararg" if is_rest else "positional",
                }
            )
        return params


# ---------------------------------------------------------------------------
# Go Scanner
# ---------------------------------------------------------------------------

_GO_FUNC_KW_RE = re.compile(r"\bfunc\b")
_GO_HTTP_HANDLEFUNC_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*HandleFunc\s*\(\s*"
    r'"(?P<pattern>[^"\n]+)"\s*,\s*(?P<handler>[A-Za-z_][A-Za-z0-9_.]*)'
)
_GO_FRAMEWORK_ROUTE_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*"
    r"(?P<method>GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|Get|Post|Put|Delete|Patch|Options|Head)"
    r'\s*\(\s*"(?P<path>[^"\n]+)"\s*,\s*(?P<handler>[A-Za-z_][A-Za-z0-9_.]*)'
)


class GoScanner:
    """Structural lexer/parser extracting Go functions, receiver methods, and HTTP routes."""

    def scan_source(
        self,
        source: str,
        module_path: str = "<go>",
        *,
        exclude_private: bool = False,
    ) -> list[FunctionSignature]:
        """Extract package functions and receiver methods from Go source."""
        masked = _mask_comments_and_strings(source, allow_backtick_strings=True)
        lines = source.splitlines()
        signatures: list[FunctionSignature] = []
        n = len(masked)

        for m in _GO_FUNC_KW_RE.finditer(masked):
            # Only top-level package declarations (brace depth 0)
            if self._brace_depth_before(masked, m.start()) != 0:
                continue

            pos = m.end()
            while pos < n and masked[pos].isspace():
                pos += 1
            if pos >= n:
                continue

            # Optional receiver: `func (s *PaymentService) Refund(...)`
            class_name: str | None = None
            if masked[pos] == "(":
                recv_end = _find_matching(masked, pos, "(", ")")
                if recv_end == -1:
                    continue
                class_name = self._parse_go_receiver(source[pos + 1 : recv_end])
                pos = recv_end + 1
                while pos < n and masked[pos].isspace():
                    pos += 1

            # Function / method identifier (skip anonymous `func(...)`)
            ident_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", masked[pos:])
            if not ident_match:
                continue
            name = ident_match.group(1)
            if exclude_private and name[0].islower():
                continue
            pos += ident_match.end()

            # Optional Go 1.18+ generic type parameter list `[T any]`
            while pos < n and masked[pos].isspace():
                pos += 1
            if pos < n and masked[pos] == "[":
                gen_end = _find_matching(masked, pos, "[", "]")
                if gen_end == -1:
                    continue
                pos = gen_end + 1
                while pos < n and masked[pos].isspace():
                    pos += 1

            if pos >= n or masked[pos] != "(":
                continue
            params_end = _find_matching(masked, pos, "(", ")")
            if params_end == -1:
                continue

            params = self._parse_go_params(source[pos + 1 : params_end])
            ret_type = self._extract_go_return_type(source, masked, params_end + 1)
            line_no = _line_of(source, m.start())

            signatures.append(
                FunctionSignature(
                    name=name,
                    module=module_path,
                    parameters=params,
                    return_type=ret_type,
                    docstring=_extract_leading_doc(lines, line_no),
                    is_async=False,
                    line_number=line_no,
                    class_name=class_name,
                    language="go",
                )
            )

        return signatures

    def scan_file(
        self,
        file_path: str | Path,
        *,
        exclude_private: bool = False,
    ) -> list[FunctionSignature]:
        """Scan a Go ``.go`` source file for function and method signatures."""
        path = Path(file_path)
        source = path.read_text(encoding="utf-8", errors="ignore")
        return self.scan_source(source, module_path=str(file_path), exclude_private=exclude_private)

    def scan_endpoints_source(
        self,
        source: str,
        module_path: str = "<go>",
    ) -> list[APIEndpoint]:
        """Extract ``net/http``, Gin, Echo, Chi, and Fiber HTTP endpoints from Go source."""
        masked = _mask_comments_and_strings(source, allow_backtick_strings=True)
        endpoints: list[APIEndpoint] = []

        # 1. net/http HandleFunc("/path", h) and Go 1.22+ HandleFunc("POST /path", h)
        for m in _GO_HTTP_HANDLEFUNC_RE.finditer(source):
            if masked[m.start() : m.start() + 2].strip() == "":
                continue
            raw_pattern = m.group("pattern").strip()
            parts = raw_pattern.split(None, 1)
            if len(parts) == 2 and parts[0].lower() in _HTTP_METHODS_LOWER:
                method, raw_path = parts[0].upper(), parts[1].strip()
            else:
                method, raw_path = "GET", raw_pattern
            endpoints.append(
                APIEndpoint(
                    path=raw_path if raw_path.startswith("/") else f"/{raw_path}",
                    method=method,
                    handler=m.group("handler"),
                    request_schema={},
                    response_schema={"type": "Any"},
                    module=module_path,
                    line_number=_line_of(source, m.start()),
                    language="go",
                )
            )

        # 2. Gin (r.GET), Echo (e.POST), Chi (r.Get), Fiber (app.Get)
        for m in _GO_FRAMEWORK_ROUTE_RE.finditer(source):
            if masked[m.start() : m.start() + 2].strip() == "":
                continue
            raw_path = m.group("path").strip()
            endpoints.append(
                APIEndpoint(
                    path=raw_path if raw_path.startswith("/") else f"/{raw_path}",
                    method=m.group("method").upper(),
                    handler=m.group("handler"),
                    request_schema={},
                    response_schema={"type": "Any"},
                    module=module_path,
                    line_number=_line_of(source, m.start()),
                    language="go",
                )
            )

        endpoints.sort(key=lambda ep: ep.line_number)
        return endpoints

    def scan_endpoints_file(self, file_path: str | Path) -> list[APIEndpoint]:
        """Scan a Go ``.go`` file for HTTP API endpoints."""
        path = Path(file_path)
        source = path.read_text(encoding="utf-8", errors="ignore")
        return self.scan_endpoints_source(source, module_path=str(file_path))

    @staticmethod
    def _brace_depth_before(masked: str, pos: int) -> int:
        return masked.count("{", 0, pos) - masked.count("}", 0, pos)

    @staticmethod
    def _parse_go_receiver(raw_recv: str) -> str | None:
        tokens = raw_recv.strip().split()
        if not tokens:
            return None
        type_tok = tokens[-1].lstrip("*")
        # Strip generic type parameters on receiver type: `Repo[T]` -> `Repo`
        bracket_idx = type_tok.find("[")
        if bracket_idx != -1:
            type_tok = type_tok[:bracket_idx]
        return type_tok or None

    @staticmethod
    def _parse_go_params(raw_params: str) -> list[dict[str, Any]]:
        """Parse Go parameters including shared-type groups (``a, b string``) and variadics."""
        raw_items = _split_top_level(raw_params, ",")
        if not raw_items:
            return []

        # Pass 1: parse each comma-separated item into (name, type_or_None)
        parsed: list[tuple[str, str | None]] = []
        for idx, item in enumerate(raw_items):
            tokens = item.strip()
            if not tokens:
                continue
            # Match leading identifier followed by whitespace and type expression
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(.+)$", tokens, flags=re.DOTALL)
            if m:
                parsed.append((m.group(1), m.group(2).strip()))
            else:
                # Could be a name sharing the next group's type (`a, b string`) OR unnamed type
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", tokens):
                    parsed.append((tokens, None))
                else:
                    parsed.append((f"arg_{idx}", tokens))

        # Pass 2: backfill shared types from right to left (`a, b string` -> both `string`)
        resolved: list[tuple[str, str]] = []
        trailing_type: str | None = None
        for idx in range(len(parsed) - 1, -1, -1):
            name, ptype = parsed[idx]
            if ptype is not None:
                trailing_type = ptype
                resolved.append((name, ptype))
            elif trailing_type is not None:
                resolved.append((name, trailing_type))
            else:
                # Unnamed single-identifier parameter type (e.g. `func(error)`)
                resolved.append((f"arg_{idx}", name))
        resolved.reverse()

        params: list[dict[str, Any]] = []
        for name, ptype in resolved:
            is_variadic = ptype.startswith("...")
            params.append(
                {
                    "name": f"*{name}" if is_variadic else name,
                    "type": ptype,
                    "default": None,
                    "kind": "vararg" if is_variadic else "positional",
                }
            )
        return params

    @staticmethod
    def _extract_go_return_type(source: str, masked: str, start: int) -> str:
        pos = start
        n = len(masked)
        while pos < n and masked[pos] in " \t":
            pos += 1
        if pos >= n or masked[pos] in "{\n":
            return "void"

        # Tuple return: `(Receipt, error)`
        if masked[pos] == "(":
            close_p = _find_matching(masked, pos, "(", ")")
            if close_p != -1:
                return source[pos : close_p + 1].strip()

        # Single return type up to `{` or newline
        i = pos
        while i < n and masked[i] not in "{\n":
            i += 1
        ret = source[pos:i].strip()
        return ret or "void"


__all__ = ["GO_EXTENSIONS", "TS_EXTENSIONS", "GoScanner", "TypeScriptScanner"]

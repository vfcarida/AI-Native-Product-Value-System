"""
AST-based Python code scanner extracting function signatures and FastAPI endpoints (EVO-017).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FunctionSignature:
    """Extracted function or method signature from Python source code."""

    name: str
    module: str
    parameters: list[dict[str, Any]]  # name, type, default, kind
    return_type: str
    docstring: str
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    line_number: int = 1
    class_name: str | None = None

    @property
    def qualified_name(self) -> str:
        """Return ``ClassName.method`` for methods or ``func`` for module functions."""
        return f"{self.class_name}.{self.name}" if self.class_name else self.name


@dataclass
class APIEndpoint:
    """Extracted HTTP API endpoint from FastAPI/Starlette route decorators."""

    path: str
    method: str
    handler: str
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]
    module: str = ""
    line_number: int = 1


_HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head"}
_IGNORED_ENDPOINT_PARAMS = {"self", "cls", "request", "response", "background_tasks"}


class CodeScanner:
    """Scan Python modules and packages for function signatures and FastAPI routes."""

    def __init__(self) -> None:
        self._ast_cache: dict[tuple[str, float], ast.Module] = {}

    def _parse_file(self, file_path: Path) -> ast.Module:
        stat = file_path.stat()
        cache_key = (str(file_path.resolve()), stat.st_mtime)
        cached = self._ast_cache.get(cache_key)
        if cached is not None:
            return cached
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
        self._ast_cache[cache_key] = tree
        return tree

    def scan_module(
        self,
        module_path: str | Path,
        *,
        exclude_private: bool = False,
    ) -> list[FunctionSignature]:
        """Parse a Python file and extract all function and method signatures."""
        path = Path(module_path)
        tree = self._parse_file(path)
        signatures: list[FunctionSignature] = []
        self._collect_functions(
            tree.body,
            module_str=str(module_path),
            class_name=None,
            signatures=signatures,
            exclude_private=exclude_private,
        )
        return signatures

    def _collect_functions(
        self,
        body: list[ast.stmt],
        *,
        module_str: str,
        class_name: str | None,
        signatures: list[FunctionSignature],
        exclude_private: bool,
    ) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                self._collect_functions(
                    node.body,
                    module_str=module_str,
                    class_name=node.name,
                    signatures=signatures,
                    exclude_private=exclude_private,
                )
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                if exclude_private and node.name.startswith("_") and not node.name.startswith("__"):
                    continue
                signatures.append(
                    self._extract_signature(
                        node,
                        module_path=module_str,
                        class_name=class_name,
                    )
                )

    def scan_directory(
        self,
        directory: str | Path,
        *,
        exclude_private: bool = False,
    ) -> list[FunctionSignature]:
        """Recursively scan all ``.py`` files in a directory or single Python file."""
        root = Path(directory)
        if root.is_file():
            return self.scan_module(root, exclude_private=exclude_private)
        if not root.exists():
            return []

        signatures: list[FunctionSignature] = []
        for py_file in sorted(root.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            try:
                signatures.extend(self.scan_module(py_file, exclude_private=exclude_private))
            except SyntaxError:
                continue
        return signatures

    def _extract_signature(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        module_path: str,
        *,
        class_name: str | None = None,
    ) -> FunctionSignature:
        """Extract parameter types, defaults, return type, docstring, and decorators."""
        args = node.args
        parameters: list[dict[str, Any]] = []

        # Positional-only + positional-or-keyword args
        pos_args = list(args.posonlyargs) + list(args.args)
        defaults_offset = len(pos_args) - len(args.defaults)

        for idx, arg in enumerate(pos_args):
            if class_name is not None and idx == 0 and arg.arg in {"self", "cls"}:
                continue
            default_val = (
                ast.unparse(args.defaults[idx - defaults_offset])
                if idx >= defaults_offset
                else None
            )
            parameters.append(
                {
                    "name": arg.arg,
                    "type": self._get_type_annotation(arg),
                    "default": default_val,
                    "kind": "positional",
                }
            )

        # *args
        if args.vararg is not None:
            parameters.append(
                {
                    "name": f"*{args.vararg.arg}",
                    "type": self._get_type_annotation(args.vararg),
                    "default": None,
                    "kind": "vararg",
                }
            )

        # Keyword-only args
        for kw_arg, kw_default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
            parameters.append(
                {
                    "name": kw_arg.arg,
                    "type": self._get_type_annotation(kw_arg),
                    "default": ast.unparse(kw_default) if kw_default is not None else None,
                    "kind": "kwonly",
                }
            )

        # **kwargs
        if args.kwarg is not None:
            parameters.append(
                {
                    "name": f"**{args.kwarg.arg}",
                    "type": self._get_type_annotation(args.kwarg),
                    "default": None,
                    "kind": "kwarg",
                }
            )

        return_type = ast.unparse(node.returns) if node.returns is not None else "Any"
        docstring = ast.get_docstring(node) or ""
        decorators = [ast.unparse(dec) for dec in node.decorator_list]

        return FunctionSignature(
            name=node.name,
            module=module_path,
            parameters=parameters,
            return_type=return_type,
            docstring=docstring,
            decorators=decorators,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            line_number=node.lineno,
            class_name=class_name,
        )

    def _get_type_annotation(self, arg: ast.arg) -> str:
        if arg.annotation is not None:
            return ast.unparse(arg.annotation)
        return "Any"

    def scan_fastapi_routes(self, app_path: str | Path) -> list[APIEndpoint]:
        """Scan a Python module for FastAPI/APIRouter route handlers."""
        path = Path(app_path)
        tree = self._parse_file(path)
        endpoints: list[APIEndpoint] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                endpoint = self._extract_fastapi_endpoint(node, module_path=str(app_path))
                if endpoint is not None:
                    endpoints.append(endpoint)
        return endpoints

    def scan_fastapi_directory(self, directory: str | Path) -> list[APIEndpoint]:
        """Recursively scan a directory or file for FastAPI route definitions."""
        root = Path(directory)
        if root.is_file():
            return self.scan_fastapi_routes(root)
        if not root.exists():
            return []

        endpoints: list[APIEndpoint] = []
        for py_file in sorted(root.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            try:
                endpoints.extend(self.scan_fastapi_routes(py_file))
            except SyntaxError:
                continue
        return endpoints

    def _extract_fastapi_endpoint(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        module_path: str = "",
    ) -> APIEndpoint | None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            method_name = decorator.func.attr.lower()
            if method_name not in _HTTP_METHODS:
                continue

            raw_path = "/"
            if decorator.args:
                raw_path = ast.unparse(decorator.args[0]).strip("'\"")
            else:
                for kw in decorator.keywords:
                    if kw.arg == "path":
                        raw_path = ast.unparse(kw.value).strip("'\"")
                        break

            return APIEndpoint(
                path=raw_path,
                method=method_name.upper(),
                handler=node.name,
                request_schema=self._extract_request_schema(node),
                response_schema=self._extract_response_schema(node),
                module=module_path,
                line_number=node.lineno,
            )
        return None

    def _extract_request_schema(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, Any]:
        schema: dict[str, Any] = {}
        all_args = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
        for arg in all_args:
            if arg.arg in _IGNORED_ENDPOINT_PARAMS:
                continue
            schema[arg.arg] = self._get_type_annotation(arg)
        return schema

    def _extract_response_schema(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict[str, Any]:
        if node.returns is not None:
            return {"type": ast.unparse(node.returns)}
        return {"type": "Any"}

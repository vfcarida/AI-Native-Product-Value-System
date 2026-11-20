"""
Specification-to-code and specification-to-test traceability linker (EVO-020).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SPEC_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\*\*ID\*\*:\s*((?:US|SCENARIO|API|NFR|DEC|REQ)-\d+)"),
    re.compile(r"^id:\s*['\"]?((?:US|SCENARIO|API|NFR|DEC|REQ)-\d+)['\"]?", re.MULTILINE),
)


@dataclass
class TraceLink:
    """Link from a Spec-Kit ID to a source code or test file location."""

    spec_id: str
    target_type: str  # "code", "test", "requirement"
    target_path: str
    line_number: int
    context: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "target_type": self.target_type,
            "target_path": self.target_path,
            "line_number": self.line_number,
            "context": self.context,
        }


class TraceabilityLinker:
    """Scan code and tests for specification ID references and report coverage."""

    def __init__(
        self,
        project_root: str | Path = ".",
        *,
        code_dirs: tuple[str, ...] = ("src",),
        test_dirs: tuple[str, ...] = ("tests",),
    ) -> None:
        self.project_root = Path(project_root)
        self.code_dirs = code_dirs
        self.test_dirs = test_dirs

    def find_spec_references(self, spec_id: str) -> list[TraceLink]:
        """Find all references to ``spec_id`` in source code and test files."""
        links: list[TraceLink] = []
        pattern = re.compile(rf"\b{re.escape(spec_id)}\b")

        for rel_dir in self.code_dirs:
            base = self.project_root / rel_dir
            if base.exists():
                for py_file in sorted(base.rglob("*.py")):
                    if "__pycache__" in py_file.parts:
                        continue
                    links.extend(self._search_file(py_file, spec_id, pattern, "code"))

        for rel_dir in self.test_dirs:
            base = self.project_root / rel_dir
            if base.exists():
                for py_file in sorted(base.rglob("*.py")):
                    if "__pycache__" in py_file.parts:
                        continue
                    links.extend(self._search_file(py_file, spec_id, pattern, "test"))

        return links

    def _search_file(
        self,
        file_path: Path,
        spec_id: str,
        pattern: re.Pattern[str],
        target_type: str,
    ) -> list[TraceLink]:
        links: list[TraceLink] = []
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return links

        rel_path = file_path.relative_to(self.project_root).as_posix()
        for line_num, line in enumerate(lines, start=1):
            if pattern.search(line):
                links.append(
                    TraceLink(
                        spec_id=spec_id,
                        target_type=target_type,
                        target_path=rel_path,
                        line_number=line_num,
                        context=line.strip(),
                    )
                )
        return links

    def generate_coverage_report(self, spec_dir: str | Path) -> dict[str, Any]:
        """Compute code and test traceability coverage across all specs in ``spec_dir``."""
        spec_path = Path(spec_dir)
        orphaned: list[str] = []
        by_spec: dict[str, dict[str, Any]] = {}
        total_specs = 0
        specs_with_code = 0
        specs_with_tests = 0

        if spec_path.exists():
            for md_file in sorted(spec_path.rglob("*.md")):
                spec_id = self._extract_spec_id(md_file)
                if not spec_id:
                    continue
                total_specs += 1
                links = self.find_spec_references(spec_id)
                code_links = [lnk for lnk in links if lnk.target_type == "code"]
                test_links = [lnk for lnk in links if lnk.target_type == "test"]
                if code_links:
                    specs_with_code += 1
                if test_links:
                    specs_with_tests += 1
                if not code_links and not test_links:
                    orphaned.append(spec_id)
                by_spec[spec_id] = {
                    "file": str(md_file),
                    "code_refs": len(code_links),
                    "test_refs": len(test_links),
                }

        coverage_pct = (
            round(((specs_with_code + specs_with_tests) / (total_specs * 2)) * 100.0, 2)
            if total_specs > 0
            else 0.0
        )
        return {
            "total_specs": total_specs,
            "specs_with_code": specs_with_code,
            "specs_with_tests": specs_with_tests,
            "orphaned_specs": orphaned,
            "coverage_percentage": coverage_pct,
            "by_spec": by_spec,
        }

    def _extract_spec_id(self, spec_file: Path) -> str:
        try:
            content = spec_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        for pattern in _SPEC_ID_PATTERNS:
            match = pattern.search(content)
            if match:
                return match.group(1)
        return ""

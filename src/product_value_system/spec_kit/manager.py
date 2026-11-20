"""
High-level Spec-Kit manager and Customer Discovery -> User Story generator (EVO-020).
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from product_value_system.spec_kit.templates import TemplateLibrary, TemplateType
from product_value_system.spec_kit.traceability import TraceabilityLinker, TraceLink
from product_value_system.spec_kit.validator import SpecValidator, ValidationResult


def _slugify(value: str) -> str:
    slug = value.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-") or "spec"


class SpecManager:
    """Create, validate, trace, and export Spec-Kit specifications."""

    def __init__(
        self,
        spec_dir: str | Path = "docs/specs",
        *,
        project_root: str | Path = ".",
        template_dir: str | Path | None = None,
    ) -> None:
        self.spec_dir = Path(spec_dir)
        self.project_root = Path(project_root)
        self.library = TemplateLibrary(template_dir=template_dir)
        self.validator = SpecValidator()
        self.linker = TraceabilityLinker(project_root=project_root)

    def _next_number(self, prefix: str) -> str:
        existing: list[int] = []
        if self.spec_dir.exists():
            pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)")
            for md_file in self.spec_dir.glob(f"{prefix}-*.md"):
                match = pattern.match(md_file.stem)
                if match:
                    existing.append(int(match.group(1)))
        return f"{max(existing, default=0) + 1:03d}"

    def create(
        self,
        template_type: TemplateType | str,
        *,
        title: str,
        spec_id: str | None = None,
        variables: dict[str, str] | None = None,
        output_path: str | Path | None = None,
    ) -> Path:
        """Render and save a new Spec-Kit specification file."""
        template = self.library.get_template(template_type)
        prefix = template.id_prefix

        if spec_id:
            num_match = re.search(r"(\d+)$", spec_id.strip())
            number = num_match.group(1) if num_match else self._next_number(prefix)
            full_id = spec_id.strip().upper()
        else:
            number = self._next_number(prefix)
            full_id = f"{prefix}-{number}"

        merged_vars: dict[str, str] = {
            "NUMBER": number,
            "TITLE": title,
            "FEATURE_NAME": title,
            "SCENARIO_NAME": f"{title} Happy Path",
            "ENDPOINT_NAME": title,
            "METHOD": "POST",
            "PATH": _slugify(title),
            "USER_ROLE": "user",
            "GOAL": title.lower(),
            "BENEFIT": "achieve desired product outcome",
            "STORY_POINTS": "5",
            "CATEGORY": "performance",
        }
        if variables:
            merged_vars.update(variables)

        rendered = self.library.render_template(template.type, merged_vars)

        if output_path is not None:
            dest = Path(output_path)
        else:
            self.spec_dir.mkdir(parents=True, exist_ok=True)
            dest = self.spec_dir / f"{full_id}-{_slugify(title)}.md"

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered, encoding="utf-8")
        return dest

    def validate(
        self,
        path: str | Path,
        spec_type: TemplateType | str | None = None,
    ) -> ValidationResult:
        """Validate a single specification file."""
        return self.validator.validate_file(path, spec_type=spec_type)

    def validate_directory(
        self,
        directory: str | Path | None = None,
    ) -> dict[str, ValidationResult]:
        """Validate every Markdown specification file under ``directory``."""
        target = Path(directory) if directory is not None else self.spec_dir
        results: dict[str, ValidationResult] = {}
        if not target.exists():
            return results
        for md_file in sorted(target.rglob("*.md")):
            if md_file.name.upper() in {"README.MD", "CATALOG.MD"}:
                continue
            results[str(md_file)] = self.validator.validate_file(md_file)
        return results

    def trace(self, spec_id: str) -> list[TraceLink]:
        """Find code and test references for ``spec_id``."""
        return self.linker.find_spec_references(spec_id)

    def coverage(self, directory: str | Path | None = None) -> dict[str, Any]:
        """Generate traceability coverage report across ``directory``."""
        target = Path(directory) if directory is not None else self.spec_dir
        return self.linker.generate_coverage_report(target)

    def export_docs(
        self,
        directory: str | Path | None = None,
        output_path: str | Path | None = None,
    ) -> str:
        """Export a consolidated Markdown specification catalog with scores and coverage."""
        target = Path(directory) if directory is not None else self.spec_dir
        validations = self.validate_directory(target)
        cov = self.coverage(target)

        lines: list[str] = [
            "# Specification Catalog",
            "",
            f"**Total Specs**: {len(validations)}  ",
            f"**Traceability Coverage**: {cov['coverage_percentage']:.1f}% "
            f"(Code: {cov['specs_with_code']}/{cov['total_specs']}, "
            f"Tests: {cov['specs_with_tests']}/{cov['total_specs']})",
            "",
            "| File | Type | Valid | Score |",
            "| :--- | :--- | :---: | :---: |",
        ]
        for file_str, res in validations.items():
            name = Path(file_str).name
            verdict = "PASS" if res.valid else "FAIL"
            lines.append(
                f"| [{name}]({name}) | {res.spec_type} | {verdict} | {res.score:.0f}/100 |"
            )

        if cov["orphaned_specs"]:
            lines.extend(["", "## Orphaned Specifications", ""])
            for orphan in cov["orphaned_specs"]:
                lines.append(f"- `{orphan}`")

        catalog_md = "\n".join(lines) + "\n"
        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(catalog_md, encoding="utf-8")
        return catalog_md


class DiscoverySpecGenerator:
    """Convert Customer Discovery insights into validated Spec-Kit user stories."""

    def __init__(
        self,
        template_library: TemplateLibrary | None = None,
        llm_callable: Callable[..., Any] | None = None,
    ) -> None:
        self.library = template_library or TemplateLibrary()
        self.llm = llm_callable

    async def generate_user_story_from_insight(
        self,
        insight: dict[str, Any],
        *,
        story_number: str = "001",
    ) -> str:
        """
        Transform a discovery insight (``pain_point``, ``job_to_be_done``, optional
        ``user_role``) into a structured Given/When/Then User Story Markdown string.
        """
        pain_point = str(insight.get("pain_point") or insight.get("problem") or "User friction")
        jtbd = str(
            insight.get("job_to_be_done")
            or insight.get("hypothesis")
            or "Complete core workflow quickly"
        )
        default_vars: dict[str, str] = {
            "NUMBER": story_number,
            "TITLE": str(insight.get("title") or jtbd[:64].strip().rstrip(".")),
            "USER_ROLE": str(insight.get("user_role") or "customer"),
            "GOAL": jtbd,
            "BENEFIT": f"eliminate friction: {pain_point}",
            "STORY_POINTS": str(insight.get("estimate", "5")),
        }

        if self.llm is not None:
            prompt = (
                "Generate user story variables as JSON from this discovery insight:\n\n"
                f"Pain Point: {pain_point}\n"
                f"Job-to-be-done: {jtbd}\n\n"
                'Output JSON: {"TITLE": "...", "USER_ROLE": "...", "GOAL": "...", "BENEFIT": "..."}'
            )
            try:
                raw = self.llm(prompt, temperature=0.3)
                if inspect.isawaitable(raw):
                    raw = await raw
                parsed = json.loads(str(raw))
                if isinstance(parsed, dict):
                    for key in ("TITLE", "USER_ROLE", "GOAL", "BENEFIT"):
                        if parsed.get(key):
                            default_vars[key] = str(parsed[key])
            except Exception:
                pass

        return self.library.render_template(TemplateType.USER_STORY, default_vars)

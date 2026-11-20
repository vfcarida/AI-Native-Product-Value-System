"""
Git-backed Architecture Decision Record (ADR) manager (EVO-018).

Supports creation, auto-numbering, YAML metadata parsing, lifecycle transitions,
keyword search, supersession graphs, code comment traceability, and onboarding logs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from product_value_system.adr.models import ADR, VALID_ADR_STATUSES, ADRMetadata, ADRStatus

_CODE_ADR_REF_RE = re.compile(r"\b(ADR-\d{4})\b", re.IGNORECASE)
_INDEX_MARKER = "<!-- ADR-INDEX-START -->"
_INDEX_END_MARKER = "<!-- ADR-INDEX-END -->"


def _normalize_adr_id(adr_id: str) -> str:
    """Normalize inputs like 'adr-1' or 'ADR-0023' to canonical 'ADR-0001' / 'ADR-0023'."""
    match = re.fullmatch(r"(?:adr[-_]?)?(\d+)", adr_id.strip(), re.IGNORECASE)
    if match:
        return f"ADR-{int(match.group(1)):04d}"
    return adr_id.strip().upper()


class ADRManager:
    """Manage Git-backed Markdown Architecture Decision Records."""

    def __init__(self, adr_dir: str | Path = "docs/adr") -> None:
        self.adr_dir = Path(adr_dir)
        self.adr_dir.mkdir(parents=True, exist_ok=True)
        readme_path = self.adr_dir / "README.md"
        if not readme_path.exists():
            self._create_readme()

    def _create_readme(self) -> None:
        readme_content = f"""# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) documenting
significant architecture decisions made in this project.

## Format

Each ADR documents:
- **Context**: What is the situation, constraints, and problem?
- **Decision**: What is the chosen architecture decision?
- **Alternatives Considered**: Pros, cons, and verdicts for evaluated options.
- **Consequences**: Positive, negative, and neutral trade-offs.

## Status Lifecycle

- **Proposed**: Draft awaiting human/team review
- **Accepted**: Approved and active architectural standard
- **Deprecated**: No longer recommended for new work
- **Superseded**: Replaced by a newer ADR

## Index

{_INDEX_MARKER}
_No ADRs recorded yet. Create one with `pvs adr new "Title"`._
{_INDEX_END_MARKER}
"""
        (self.adr_dir / "README.md").write_text(readme_content, encoding="utf-8")

    def create(
        self,
        title: str,
        author: str,
        context: str = "",
        decision: str = "",
        status: str = ADRStatus.PROPOSED.value,
        *,
        alternatives: list[dict[str, str]] | None = None,
        consequences: dict[str, list[str]] | None = None,
        implementation_plan: str = "",
        validation: list[str] | None = None,
        references: list[str] | None = None,
        related_requirements: list[str] | None = None,
        related_code: list[str] | None = None,
        related_prs: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> ADR:
        """Create and persist a new auto-numbered ADR Markdown file."""
        adr_id = self._generate_id()
        authors = [a.strip().lstrip("@") for a in author.split(",") if a.strip()] or ["unknown"]
        metadata = ADRMetadata(
            id=adr_id,
            status=status,
            date=datetime.now(UTC).strftime("%Y-%m-%d"),
            authors=authors,
            supersedes=[],
            related_requirements=list(related_requirements or []),
            related_code=list(related_code or []),
            related_prs=list(related_prs or []),
            tags=list(tags or []),
        )
        normalized_consequences = {
            "positive": list((consequences or {}).get("positive", [])),
            "negative": list((consequences or {}).get("negative", [])),
            "neutral": list((consequences or {}).get("neutral", [])),
        }
        slug = self._slugify(title) or "decision"
        file_path = self.adr_dir / f"{adr_id}-{slug}.md"

        adr = ADR(
            metadata=metadata,
            title=title.strip(),
            context=context or "<!-- Describe the context, constraints, and problem -->",
            decision=decision or "<!-- Describe the architectural decision -->",
            alternatives=list(alternatives or []),
            consequences=normalized_consequences,
            implementation_plan=implementation_plan or "<!-- Outline implementation phases -->",
            validation=list(validation or []),
            references=list(references or []),
            file_path=str(file_path),
        )
        self._write_adr(adr)
        self.refresh_index()
        return adr

    def _generate_id(self) -> str:
        existing_ids: list[int] = []
        for file_path in self.adr_dir.glob("ADR-*.md"):
            match = re.match(r"ADR-(\d+)", file_path.stem)
            if match:
                existing_ids.append(int(match.group(1)))
        next_num = max(existing_ids, default=0) + 1
        return f"ADR-{next_num:04d}"

    def _slugify(self, title: str) -> str:
        slug = title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_-]+", "-", slug)
        return slug.strip("-")

    def _write_adr(self, adr: ADR) -> None:
        lines: list[str] = [
            f"# {adr.metadata.id}: {adr.title}",
            "",
            f"**Status**: {adr.metadata.status.title()}  ",
            f"**Date**: {adr.metadata.date}  ",
            f"**Authors**: {', '.join(f'@{a}' for a in adr.metadata.authors)}  ",
        ]
        if adr.metadata.supersedes:
            lines.append(
                f"**Related ADRs**: Supersedes {', '.join(adr.metadata.supersedes)}  "
            )
        if adr.metadata.related_requirements:
            lines.append(
                f"**Related Requirements**: {', '.join(adr.metadata.related_requirements)}  "
            )
        if adr.metadata.related_code:
            code_refs = ", ".join(f"`{c}`" for c in adr.metadata.related_code)
            lines.append(f"**Related Code**: {code_refs}  ")
        if adr.metadata.related_prs:
            lines.append(f"**Related PRs**: {', '.join(adr.metadata.related_prs)}  ")

        lines.extend(
            [
                "",
                "## Context",
                "",
                adr.context,
                "",
                "## Decision",
                "",
                adr.decision,
            ]
        )

        if adr.alternatives:
            lines.extend(["", "## Alternatives Considered"])
            for idx, alt in enumerate(adr.alternatives, start=1):
                lines.extend(["", f"### {idx}. {alt['name']}"])
                if alt.get("pros"):
                    lines.extend(["", "**Pros**:", alt["pros"]])
                if alt.get("cons"):
                    lines.extend(["", "**Cons**:", alt["cons"]])
                if alt.get("verdict"):
                    lines.extend(["", f"**Verdict**: {alt['verdict']}"])

        lines.extend(["", "## Consequences"])
        for category in ("positive", "negative", "neutral"):
            items = adr.consequences.get(category, [])
            if items:
                lines.extend(["", f"### {category.title()}", ""])
                for item in items:
                    lines.append(f"- {item}")

        if adr.implementation_plan:
            lines.extend(["", "## Implementation Plan", "", adr.implementation_plan])

        if adr.validation:
            lines.extend(["", "## Validation", ""])
            for item in adr.validation:
                lines.append(f"- [ ] {item}")

        if adr.references:
            lines.extend(["", "## References", ""])
            for ref in adr.references:
                lines.append(f"- {ref}")

        yaml_block = yaml.safe_dump(adr.metadata.to_dict(), sort_keys=False).strip()
        lines.extend(["", "## Metadata", "", "```yaml", yaml_block, "```", ""])

        Path(adr.file_path).write_text("\n".join(lines), encoding="utf-8")

    def list_adrs(
        self,
        *,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[ADR]:
        """List all ADRs sorted by ID, optionally filtered by status and/or tag."""
        status_norm = status.strip().lower() if status else None
        tag_norm = tag.strip().lower() if tag else None
        adrs: list[ADR] = []

        for file_path in sorted(self.adr_dir.glob("ADR-*.md")):
            adr = self._parse_adr(file_path)
            if status_norm and adr.metadata.status != status_norm:
                continue
            if tag_norm and tag_norm not in {t.lower() for t in adr.metadata.tags}:
                continue
            adrs.append(adr)
        return adrs

    def _parse_adr(self, file_path: Path) -> ADR:
        content = file_path.read_text(encoding="utf-8")

        yaml_match = re.search(r"## Metadata\s+```yaml\s*\n(.*?)```", content, re.DOTALL)
        if not yaml_match:
            yaml_match = re.search(r"```yaml\s*\n(.*?)```", content, re.DOTALL)
        if yaml_match:
            parsed_meta = yaml.safe_load(yaml_match.group(1))
            if isinstance(parsed_meta, dict) and "id" in parsed_meta:
                metadata = ADRMetadata.from_dict(parsed_meta)
            else:
                metadata = self._parse_metadata_from_header(content, file_path)
        else:
            metadata = self._parse_metadata_from_header(content, file_path)

        title_match = re.search(r"^#\s+(ADR-\d+):\s+(.+)$", content, re.MULTILINE)
        title = title_match.group(2).strip() if title_match else file_path.stem

        return ADR(
            metadata=metadata,
            title=title,
            context=self._extract_section(content, "Context"),
            decision=self._extract_section(content, "Decision"),
            alternatives=self._parse_alternatives(content),
            consequences=self._parse_consequences(content),
            implementation_plan=self._extract_section(content, "Implementation Plan"),
            validation=self._parse_validation(content),
            references=self._parse_references(content),
            file_path=str(file_path),
        )

    def _extract_section(self, content: str, section_name: str) -> str:
        pattern = rf"^## {re.escape(section_name)}\s*\n+(.*?)(?=^## |\Z)"
        match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _parse_alternatives(self, content: str) -> list[dict[str, str]]:
        alternatives: list[dict[str, str]] = []
        alt_section = self._extract_section(content, "Alternatives Considered")
        if not alt_section:
            return alternatives

        pattern = r"^###\s+(?:\d+\.\s+)?(.+?)\n(.*?)(?=^###\s+|\Z)"
        for match in re.finditer(pattern, alt_section, re.DOTALL | re.MULTILINE):
            name = match.group(1).strip()
            body = match.group(2).strip()
            alt: dict[str, str] = {"name": name}

            pros_m = re.search(r"\*\*Pros\*\*:\s*\n(.*?)(?=\n\*\*|\Z)", body, re.DOTALL)
            if pros_m:
                alt["pros"] = pros_m.group(1).strip()
            cons_m = re.search(r"\*\*Cons\*\*:\s*\n(.*?)(?=\n\*\*|\Z)", body, re.DOTALL)
            if cons_m:
                alt["cons"] = cons_m.group(1).strip()
            verdict_m = re.search(r"\*\*Verdict\*\*:\s*(.+)", body)
            if verdict_m:
                alt["verdict"] = verdict_m.group(1).strip()
            alternatives.append(alt)
        return alternatives

    def _parse_consequences(self, content: str) -> dict[str, list[str]]:
        consequences: dict[str, list[str]] = {"positive": [], "negative": [], "neutral": []}
        cons_section = self._extract_section(content, "Consequences")
        if not cons_section:
            return consequences

        for category in ("positive", "negative", "neutral"):
            pattern = rf"^### {category.title()}\s*\n(.*?)(?=^### |\Z)"
            match = re.search(pattern, cons_section, re.DOTALL | re.IGNORECASE | re.MULTILINE)
            if match:
                consequences[category] = re.findall(r"^- (.+)$", match.group(1), re.MULTILINE)
        return consequences

    def _parse_validation(self, content: str) -> list[str]:
        section = self._extract_section(content, "Validation")
        return re.findall(r"^- \[[ xX]\] (.+)$", section, re.MULTILINE)

    def _parse_references(self, content: str) -> list[str]:
        section = self._extract_section(content, "References")
        return re.findall(r"^- (.+)$", section, re.MULTILINE)

    def _parse_metadata_from_header(self, content: str, file_path: Path) -> ADRMetadata:
        id_match = re.search(r"(ADR-\d+)", file_path.stem)
        adr_id = id_match.group(1) if id_match else "ADR-0000"

        status = ADRStatus.PROPOSED.value
        date = datetime.now(UTC).strftime("%Y-%m-%d")
        authors: list[str] = []

        status_m = re.search(r"\*\*Status\*\*:\s*(\w+)", content)
        if status_m and status_m.group(1).lower() in VALID_ADR_STATUSES:
            status = status_m.group(1).lower()
        date_m = re.search(r"\*\*Date\*\*:\s*([\d-]+)", content)
        if date_m:
            date = date_m.group(1)
        authors_m = re.search(r"\*\*Authors\*\*:\s*(.+)", content)
        if authors_m:
            authors = [a.strip().lstrip("@") for a in authors_m.group(1).split(",") if a.strip()]

        return ADRMetadata(id=adr_id, status=status, date=date, authors=authors)

    def get(self, adr_id: str) -> ADR | None:
        """Retrieve an ADR by its identifier (e.g., ``ADR-0001``)."""
        canonical = _normalize_adr_id(adr_id)
        for file_path in sorted(self.adr_dir.glob(f"{canonical}-*.md")):
            return self._parse_adr(file_path)
        exact = self.adr_dir / f"{canonical}.md"
        if exact.exists():
            return self._parse_adr(exact)
        return None

    def update_status(
        self,
        adr_id: str,
        new_status: str,
        *,
        reviewer: str | None = None,
    ) -> ADR:
        """Transition ADR lifecycle status and optionally record approving reviewer."""
        adr = self.get(adr_id)
        if adr is None:
            raise ValueError(f"ADR {adr_id} not found")
        normalized = new_status.strip().lower()
        if normalized not in VALID_ADR_STATUSES:
            raise ValueError(
                f"Invalid ADR status {new_status!r}; expected one of {sorted(VALID_ADR_STATUSES)}"
            )
        adr.metadata.status = normalized
        if reviewer:
            clean_reviewer = reviewer.strip().lstrip("@")
            if clean_reviewer and clean_reviewer not in adr.metadata.authors:
                adr.metadata.authors.append(clean_reviewer)
        self._write_adr(adr)
        self.refresh_index()
        return adr

    def accept(self, adr_id: str, *, reviewer: str | None = None) -> ADR:
        """Approve a proposed ADR after human review."""
        return self.update_status(adr_id, ADRStatus.ACCEPTED.value, reviewer=reviewer)

    def deprecate(self, adr_id: str) -> ADR:
        """Mark an ADR as deprecated."""
        return self.update_status(adr_id, ADRStatus.DEPRECATED.value)

    def supersede(self, old_adr_id: str, new_adr_id: str) -> tuple[ADR, ADR]:
        """Mark ``old_adr_id`` as superseded by ``new_adr_id`` and link both records."""
        old_adr = self.get(old_adr_id)
        if old_adr is None:
            raise ValueError(f"ADR {old_adr_id} not found")
        new_adr = self.get(new_adr_id)
        if new_adr is None:
            raise ValueError(f"ADR {new_adr_id} not found")

        old_adr.metadata.status = ADRStatus.SUPERSEDED.value
        self._write_adr(old_adr)

        if old_adr.metadata.id not in new_adr.metadata.supersedes:
            new_adr.metadata.supersedes.append(old_adr.metadata.id)
        self._write_adr(new_adr)
        self.refresh_index()
        return old_adr, new_adr

    def link_code(self, adr_id: str, code_paths: list[str]) -> ADR:
        """Link implementation file paths to an ADR."""
        adr = self.get(adr_id)
        if adr is None:
            raise ValueError(f"ADR {adr_id} not found")
        for path in code_paths:
            if path not in adr.metadata.related_code:
                adr.metadata.related_code.append(path)
        self._write_adr(adr)
        return adr

    def link_requirements(self, adr_id: str, requirements: list[str]) -> ADR:
        """Link requirement IDs (e.g., REQ-SEC-001) to an ADR."""
        adr = self.get(adr_id)
        if adr is None:
            raise ValueError(f"ADR {adr_id} not found")
        for req in requirements:
            if req not in adr.metadata.related_requirements:
                adr.metadata.related_requirements.append(req)
        self._write_adr(adr)
        return adr

    def link_pr(self, adr_id: str, pr_ref: str) -> ADR:
        """Link a pull request identifier or URL to an ADR."""
        adr = self.get(adr_id)
        if adr is None:
            raise ValueError(f"ADR {adr_id} not found")
        if pr_ref not in adr.metadata.related_prs:
            adr.metadata.related_prs.append(pr_ref)
        self._write_adr(adr)
        return adr

    def search(self, query: str) -> list[ADR]:
        """Search ADRs case-insensitively across title, body, tags, requirements, and code."""
        query_lower = query.strip().lower()
        if not query_lower:
            return self.list_adrs()
        results: list[ADR] = []
        for adr in self.list_adrs():
            alt_text = " ".join(
                f"{a.get('name', '')} {a.get('pros', '')} {a.get('cons', '')} "
                f"{a.get('verdict', '')}"
                for a in adr.alternatives
            )
            searchable = " ".join(
                [
                    adr.metadata.id,
                    adr.title,
                    adr.context,
                    adr.decision,
                    alt_text,
                    " ".join(adr.metadata.tags),
                    " ".join(adr.metadata.related_requirements),
                    " ".join(adr.metadata.related_code),
                ]
            ).lower()
            if query_lower in searchable:
                results.append(adr)
        return results

    def scan_code_references(self, code_paths: list[str | Path]) -> dict[str, list[str]]:
        """
        Scan source files for inline ADR comments (e.g., ``# See ADR-0001``) and
        return mapping ``{adr_id: ["path/to/file.py:L42", ...]}``.
        """
        refs: dict[str, list[str]] = {}
        for cp in code_paths:
            root = Path(cp)
            files = (
                [root]
                if root.is_file()
                else (sorted(root.rglob("*.py")) if root.exists() else [])
            )
            for py_file in files:
                if "__pycache__" in py_file.parts or not py_file.is_file():
                    continue
                for line_no, line in enumerate(
                    py_file.read_text(encoding="utf-8", errors="ignore").splitlines(),
                    start=1,
                ):
                    for match in _CODE_ADR_REF_RE.finditer(line):
                        adr_id = match.group(1).upper()
                        refs.setdefault(adr_id, []).append(f"{py_file}:L{line_no}")
        return refs

    def build_decision_graph(self) -> str:
        """Render a Mermaid flowchart visualizing ADRs and supersession links."""
        adrs = self.list_adrs()
        lines = ["```mermaid", "flowchart LR"]
        if not adrs:
            lines.append('    EMPTY["No ADRs recorded"]')
            lines.append("```")
            return "\n".join(lines)

        for adr in adrs:
            node_id = adr.metadata.id.replace("-", "_")
            safe_title = adr.title.replace('"', "'")
            lines.append(
                f'    {node_id}["{adr.metadata.id}: {safe_title} ({adr.metadata.status})"]'
            )
        for adr in adrs:
            new_node = adr.metadata.id.replace("-", "_")
            for old_id in adr.metadata.supersedes:
                old_node = _normalize_adr_id(old_id).replace("-", "_")
                lines.append(f"    {new_node} -->|supersedes| {old_node}")
        lines.append("```")
        return "\n".join(lines)

    def generate_decision_log(self, output_path: str | Path | None = None) -> str:
        """Generate a consolidated Markdown decision log for team onboarding."""
        adrs = self.list_adrs()
        counts = {s.value: 0 for s in ADRStatus}
        for adr in adrs:
            counts[adr.metadata.status] = counts.get(adr.metadata.status, 0) + 1

        lines: list[str] = [
            "# Architecture Decision Log",
            "",
            f"**Total ADRs**: {len(adrs)} "
            f"(Accepted: {counts['accepted']}, Proposed: {counts['proposed']}, "
            f"Superseded: {counts['superseded']}, Deprecated: {counts['deprecated']})",
            "",
            "## Decision Graph",
            "",
            self.build_decision_graph(),
            "",
            "## Chronological Index",
            "",
            "| ID | Status | Date | Title | Supersedes |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for adr in adrs:
            rel_name = Path(adr.file_path).name
            sup = ", ".join(adr.metadata.supersedes) or "—"
            lines.append(
                f"| [{adr.metadata.id}]({rel_name}) | {adr.metadata.status} | "
                f"{adr.metadata.date} | {adr.title} | {sup} |"
            )

        lines.extend(["", "## Decision Summaries", ""])
        for adr in adrs:
            first_decision_line = (adr.decision.splitlines() or [""])[0].strip()
            lines.extend(
                [
                    f"### {adr.metadata.id}: {adr.title} ({adr.metadata.status})",
                    "",
                    f"- **Date**: {adr.metadata.date}",
                    f"- **Authors**: {', '.join(adr.metadata.authors)}",
                    f"- **Decision**: {first_decision_line}",
                    "",
                ]
            )

        log_md = "\n".join(lines)
        if output_path is not None:
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(log_md, encoding="utf-8")
        return log_md

    def refresh_index(self) -> None:
        """Update the auto-generated ADR table inside ``docs/adr/README.md``."""
        readme_path = self.adr_dir / "README.md"
        if not readme_path.exists():
            self._create_readme()

        adrs = self.list_adrs()
        if adrs:
            table_lines = [
                "| ID | Status | Date | Title |",
                "| :--- | :--- | :--- | :--- |",
            ]
            for adr in adrs:
                rel_name = Path(adr.file_path).name
                table_lines.append(
                    f"| [{adr.metadata.id}]({rel_name}) | {adr.metadata.status} | "
                    f"{adr.metadata.date} | {adr.title} |"
                )
            table_md = "\n".join(table_lines)
        else:
            table_md = '_No ADRs recorded yet. Create one with `pvs adr new "Title"`._'

        content = readme_path.read_text(encoding="utf-8")
        replacement = f"{_INDEX_MARKER}\n{table_md}\n{_INDEX_END_MARKER}"
        if _INDEX_MARKER in content and _INDEX_END_MARKER in content:
            updated = re.sub(
                rf"{re.escape(_INDEX_MARKER)}.*?{re.escape(_INDEX_END_MARKER)}",
                replacement,
                content,
                flags=re.DOTALL,
            )
        else:
            updated = content.rstrip() + "\n\n" + replacement + "\n"
        readme_path.write_text(updated, encoding="utf-8")


def convert_design_doc_to_adr(
    input_path: str | Path,
    output_adr_dir: str | Path = "docs/adr",
    *,
    author: str = "team",
    status: str = ADRStatus.ACCEPTED.value,
) -> ADR:
    """Migrate an existing Markdown RFC / design doc into a numbered ADR."""
    src = Path(input_path)
    raw = src.read_text(encoding="utf-8")
    title_m = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else src.stem.replace("-", " ").title()

    manager = ADRManager(adr_dir=output_adr_dir)
    return manager.create(
        title=title,
        author=author,
        context=f"Migrated from design document `{src.name}`.\n\n{raw.strip()}",
        decision=f"Adopt the architecture documented in `{src.name}`.",
        status=status,
        references=[f"Original design doc: `{src}`"],
        tags=["migrated"],
    )

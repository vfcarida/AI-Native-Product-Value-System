"""
Unit tests for EVO-018: ADRManager CRUD, YAML metadata, search, supersession,
code references, Mermaid graphs, onboarding logs, and repo example ADRs.
"""

from pathlib import Path

import pytest

from product_value_system.adr import ADRManager, convert_design_doc_to_adr

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_create_adr_auto_numbers_and_writes_template(tmp_path: Path) -> None:
    """Creating ADRs auto-numbers ADR-0001, ADR-0002 and writes Markdown + YAML metadata."""
    mgr = ADRManager(adr_dir=tmp_path / "docs" / "adr")

    adr1 = mgr.create(
        title="Use Cedar for Policy Engine",
        author="alice",
        context="We need declarative policy evaluation.",
        decision="We will use AWS Cedar.",
        status="proposed",
        related_requirements=["REQ-SEC-001"],
        related_code=["src/policy/engine.py"],
        tags=["security", "cedar"],
    )
    adr2 = mgr.create(title="Adopt OpenTelemetry", author="bob")

    assert adr1.metadata.id == "ADR-0001"
    assert adr2.metadata.id == "ADR-0002"
    assert adr1.metadata.status == "proposed"
    assert adr1.metadata.authors == ["alice"]
    assert Path(adr1.file_path).exists()
    content = Path(adr1.file_path).read_text(encoding="utf-8")
    assert "# ADR-0001: Use Cedar for Policy Engine" in content
    assert "REQ-SEC-001" in content
    assert "```yaml" in content


@pytest.mark.unit
def test_full_adr_roundtrip_parses_alternatives_consequences_and_validation(
    tmp_path: Path,
) -> None:
    """All structured sections survive write -> reload round-trip accurately."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    created = mgr.create(
        title="Use Postgres with pgvector",
        author="alice, bob",
        context="Need relational + vector retrieval.",
        decision="Adopt PostgreSQL with pgvector.",
        status="accepted",
        alternatives=[
            {
                "name": "Qdrant",
                "pros": "- Fast vector search",
                "cons": "- Separate database cluster to operate",
                "verdict": "Rejected due to operational complexity.",
            }
        ],
        consequences={
            "positive": ["Single operational database"],
            "negative": ["Lower max recall at 100M vectors"],
            "neutral": ["Standard SQL migrations"],
        },
        implementation_plan="Phase 1: Enable extension.\nPhase 2: Add HNSW index.",
        validation=["p95 vector query < 20ms", "Backup/restore tested"],
        references=["Experiment: EXP-VEC-001"],
        related_prs=["PR#142"],
        tags=["database", "vectors"],
    )

    loaded = mgr.get(created.metadata.id)
    assert loaded is not None
    assert loaded.title == "Use Postgres with pgvector"
    assert loaded.metadata.authors == ["alice", "bob"]
    assert loaded.metadata.related_prs == ["PR#142"]
    assert len(loaded.alternatives) == 1
    assert loaded.alternatives[0]["name"] == "Qdrant"
    assert "Rejected" in loaded.alternatives[0]["verdict"]
    assert loaded.consequences["positive"] == ["Single operational database"]
    assert loaded.consequences["negative"] == ["Lower max recall at 100M vectors"]
    assert loaded.validation == ["p95 vector query < 20ms", "Backup/restore tested"]
    assert loaded.references == ["Experiment: EXP-VEC-001"]


@pytest.mark.unit
def test_header_fallback_parser_handles_adr_without_yaml_block(tmp_path: Path) -> None:
    """ADRs written without a YAML Metadata block still parse cleanly from header lines."""
    adr_dir = tmp_path / "adr"
    mgr = ADRManager(adr_dir=adr_dir)
    manual = adr_dir / "ADR-0009-legacy-record.md"
    manual.write_text(
        """# ADR-0009: Legacy Manual Record

**Status**: Accepted
**Date**: 2025-06-01
**Authors**: @carol, @dave

## Context

Legacy context text.

## Decision

Keep simple architecture.
""",
        encoding="utf-8",
    )

    parsed = mgr.get("ADR-0009")
    assert parsed is not None
    assert parsed.metadata.id == "ADR-0009"
    assert parsed.metadata.status == "accepted"
    assert parsed.metadata.date == "2025-06-01"
    assert parsed.metadata.authors == ["carol", "dave"]
    assert parsed.context == "Legacy context text."


@pytest.mark.unit
def test_list_adrs_filters_by_status_and_tag(tmp_path: Path) -> None:
    """list_adrs supports filtering by status and tag."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    mgr.create("Decision 1", "alice", status="accepted", tags=["security"])
    mgr.create("Decision 2", "bob", status="proposed", tags=["storage"])
    mgr.create("Decision 3", "alice", status="accepted", tags=["storage"])

    assert len(mgr.list_adrs()) == 3
    assert len(mgr.list_adrs(status="accepted")) == 2
    assert len(mgr.list_adrs(status="proposed")) == 1
    assert len(mgr.list_adrs(tag="storage")) == 2
    assert len(mgr.list_adrs(status="accepted", tag="storage")) == 1


@pytest.mark.unit
def test_get_adr_supports_shorthand_and_returns_none_when_missing(tmp_path: Path) -> None:
    """get() normalizes shorthand IDs ('adr-1' -> 'ADR-0001') and returns None if absent."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    mgr.create("First Decision", "alice")

    assert mgr.get("ADR-0001") is not None
    assert mgr.get("adr-1") is not None
    assert mgr.get("ADR-9999") is None


@pytest.mark.unit
def test_status_lifecycle_accept_deprecate_and_invalid_rejected(tmp_path: Path) -> None:
    """Status transitions proposed -> accepted -> deprecated work and reject invalid statuses."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    adr = mgr.create("Use Feature Flags", "alice", status="proposed")

    accepted = mgr.accept(adr.metadata.id, reviewer="bob")
    assert accepted.metadata.status == "accepted"
    assert "bob" in accepted.metadata.authors

    deprecated = mgr.deprecate(adr.metadata.id)
    assert deprecated.metadata.status == "deprecated"

    with pytest.raises(ValueError, match="Invalid ADR status"):
        mgr.update_status(adr.metadata.id, "invalid-status")

    with pytest.raises(ValueError, match="not found"):
        mgr.accept("ADR-9999")


@pytest.mark.unit
def test_supersede_updates_both_adrs_and_handles_missing(tmp_path: Path) -> None:
    """supersede() marks old ADR superseded and links supersedes on the new ADR."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    adr1 = mgr.create("Old Decision", "alice", status="accepted")
    adr2 = mgr.create("New Decision", "bob", status="proposed")

    mgr.supersede(adr1.metadata.id, adr2.metadata.id)

    reloaded_old = mgr.get(adr1.metadata.id)
    reloaded_new = mgr.get(adr2.metadata.id)
    assert reloaded_old is not None and reloaded_old.metadata.status == "superseded"
    assert reloaded_new is not None and adr1.metadata.id in reloaded_new.metadata.supersedes

    # Calling supersede again does not duplicate ID in supersedes list
    mgr.supersede(adr1.metadata.id, adr2.metadata.id)
    reloaded_new_2 = mgr.get(adr2.metadata.id)
    assert reloaded_new_2 is not None
    assert reloaded_new_2.metadata.supersedes.count(adr1.metadata.id) == 1

    with pytest.raises(ValueError, match="not found"):
        mgr.supersede("ADR-9999", adr2.metadata.id)


@pytest.mark.unit
def test_search_adrs_matches_title_context_tags_and_requirements(tmp_path: Path) -> None:
    """search() finds matches across title, context, decision, tags, and requirements."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    mgr.create("Use Cedar", "alice", context="Policy engine for authorization", tags=["iam"])
    mgr.create("Use PostgreSQL", "bob", context="Primary database", related_requirements=["REQ-9"])
    mgr.create("Use OPA", "charlie", context="Alternative policy engine")

    policy_hits = mgr.search("policy")
    assert len(policy_hits) == 2
    assert len(mgr.search("REQ-9")) == 1
    assert len(mgr.search("iam")) == 1
    assert mgr.search("nonexistent-term") == []


@pytest.mark.unit
def test_link_code_requirements_and_prs(tmp_path: Path) -> None:
    """Linking code paths, requirements, and PRs persists deduplicated references."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    adr = mgr.create("Sandbox Isolation", "alice")

    mgr.link_code(adr.metadata.id, ["src/runtime/sandbox.py", "src/runtime/sandbox.py"])
    mgr.link_requirements(adr.metadata.id, ["REQ-SEC-010"])
    mgr.link_pr(adr.metadata.id, "PR#77")

    reloaded = mgr.get(adr.metadata.id)
    assert reloaded is not None
    assert reloaded.metadata.related_code == ["src/runtime/sandbox.py"]
    assert reloaded.metadata.related_requirements == ["REQ-SEC-010"]
    assert reloaded.metadata.related_prs == ["PR#77"]


@pytest.mark.unit
def test_scan_code_references_finds_inline_adr_comments(tmp_path: Path) -> None:
    """scan_code_references locates `# See ADR-XXXX` comments across Python files."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "policy.py").write_text(
        "# See ADR-0001 for Cedar engine selection\n"
        "def authorize() -> bool:\n"
        "    # Also follows ADR-0003\n"
        "    return True\n",
        encoding="utf-8",
    )
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    refs = mgr.scan_code_references([src_dir])

    assert "ADR-0001" in refs and len(refs["ADR-0001"]) == 1
    assert refs["ADR-0001"][0].endswith(":L1")
    assert "ADR-0003" in refs and refs["ADR-0003"][0].endswith(":L3")


@pytest.mark.unit
def test_build_decision_graph_renders_mermaid_supersession_edges(tmp_path: Path) -> None:
    """build_decision_graph produces a Mermaid flowchart with supersedes arrows."""
    mgr = ADRManager(adr_dir=tmp_path / "adr")
    a1 = mgr.create("Use REST", "alice", status="accepted")
    a2 = mgr.create("Use GraphQL", "bob", status="accepted")
    mgr.supersede(a1.metadata.id, a2.metadata.id)

    graph = mgr.build_decision_graph()
    assert "```mermaid" in graph
    assert "flowchart LR" in graph
    assert "ADR_0002 -->|supersedes| ADR_0001" in graph


@pytest.mark.unit
def test_generate_decision_log_and_readme_auto_index(tmp_path: Path) -> None:
    """Decision log writes onboarding summary and README.md maintains auto-updated index."""
    adr_dir = tmp_path / "adr"
    mgr = ADRManager(adr_dir=adr_dir)
    mgr.create("Decision Alpha", "alice", decision="Adopt Alpha.", status="accepted")
    mgr.create("Decision Beta", "bob", decision="Adopt Beta.", status="proposed")

    readme = (adr_dir / "README.md").read_text(encoding="utf-8")
    assert "ADR-0001" in readme and "ADR-0002" in readme

    log_path = tmp_path / "DECISION_LOG.md"
    log_md = mgr.generate_decision_log(output_path=log_path)
    assert log_path.exists()
    assert "**Total ADRs**: 2" in log_md
    assert "Decision Alpha" in log_md
    assert "```mermaid" in log_md


@pytest.mark.unit
def test_convert_design_doc_to_adr_migrates_rfc(tmp_path: Path) -> None:
    """convert_design_doc_to_adr migrates an existing RFC Markdown file into an ADR."""
    rfc = tmp_path / "rfc-042-graphql.md"
    rfc.write_text("# Migrate API to GraphQL\n\nDetailed RFC body.\n", encoding="utf-8")

    adr = convert_design_doc_to_adr(rfc, output_adr_dir=tmp_path / "adr", author="arch-team")
    assert adr.metadata.id == "ADR-0001"
    assert adr.title == "Migrate API to GraphQL"
    assert "migrated" in adr.metadata.tags
    assert "Detailed RFC body." in adr.context


@pytest.mark.unit
def test_repo_example_adrs_parse_cleanly_with_complete_sections() -> None:
    """All 3 bundled example ADRs in docs/adr/ parse cleanly with full rationale."""
    mgr = ADRManager(adr_dir=REPO_ROOT / "docs" / "adr")
    adrs = mgr.list_adrs()
    assert len(adrs) >= 3
    ids = {a.metadata.id for a in adrs}
    assert {"ADR-0001", "ADR-0002", "ADR-0003"}.issubset(ids)
    for adr in adrs:
        assert adr.context
        assert adr.decision
        assert len(adr.alternatives) >= 2
        assert adr.consequences["positive"]
        assert adr.validation


@pytest.mark.unit
def test_adr_to_dict_and_empty_decision_graph(tmp_path: Path) -> None:
    """Empty directory renders placeholder Mermaid node, and ADR.to_dict serializes cleanly."""
    mgr = ADRManager(adr_dir=tmp_path / "empty_adr")
    empty_graph = mgr.build_decision_graph()
    assert "No ADRs recorded" in empty_graph

    adr = mgr.create("Serialize Test", "alice", context="Ctx", decision="Dec")
    payload = adr.to_dict()
    assert payload["metadata"]["id"] == "ADR-0001"
    assert payload["title"] == "Serialize Test"
    assert payload["context"] == "Ctx"

"""
Unit tests for EVO-020 Spec-Kit TemplateLibrary, SpecManager, and VS Code snippets.
"""

import json
from pathlib import Path

import pytest

from product_value_system.spec_kit import (
    SpecManager,
    SpecValidator,
    TemplateLibrary,
    TemplateType,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit
def test_builtin_templates_include_all_5_spec_types() -> None:
    """TemplateLibrary exposes user_story, scenario, api_contract, nfr, and decision."""
    lib = TemplateLibrary()
    types = {t.type for t in lib.list_templates()}
    assert types == {
        TemplateType.USER_STORY,
        TemplateType.SCENARIO,
        TemplateType.API_CONTRACT,
        TemplateType.NFR,
        TemplateType.DECISION,
    }


@pytest.mark.unit
def test_template_type_from_cli_resolves_aliases_and_rejects_unknown() -> None:
    """TemplateType.from_cli maps hyphenated aliases and raises ValueError on unknown."""
    assert TemplateType.from_cli("user-story") == TemplateType.USER_STORY
    assert TemplateType.from_cli("api-contract") == TemplateType.API_CONTRACT
    assert TemplateType.from_cli("nfr") == TemplateType.NFR
    assert TemplateType.from_cli("scenario") == TemplateType.SCENARIO
    assert TemplateType.from_cli("decision") == TemplateType.DECISION
    with pytest.raises(ValueError, match="Unknown spec template type"):
        TemplateType.from_cli("invalid-type")


@pytest.mark.unit
def test_render_all_5_builtin_templates_pass_validator() -> None:
    """Every built-in Spec-Kit template renders valid Markdown scoring >=90."""
    lib = TemplateLibrary()
    validator = SpecValidator()
    common_vars = {
        "NUMBER": "042",
        "TITLE": "Biometric PIX Transfer",
        "USER_ROLE": "customer",
        "GOAL": "confirm transfers via fingerprint",
        "BENEFIT": "prevent unauthorized transfers",
        "STORY_POINTS": "5",
        "FEATURE_NAME": "Biometric PIX Transfer",
        "SCENARIO_NAME": "Successful biometric confirmation",
        "ENDPOINT_NAME": "Create Transfer",
        "METHOD": "POST",
        "PATH": "transfers",
        "CATEGORY": "security",
    }

    for ttype in TemplateType:
        rendered = lib.render_template(ttype, common_vars)
        result = validator.validate(rendered, spec_type=ttype)
        assert result.valid is True, f"Template {ttype} failed validation: {result.issues}"
        assert result.score >= 90.0


@pytest.mark.unit
def test_custom_template_directory_overrides_builtin(tmp_path: Path) -> None:
    """Custom `<template_type>.md` file overrides the built-in template."""
    custom_dir = tmp_path / "templates"
    custom_dir.mkdir()
    (custom_dir / "user_story.md").write_text("# Custom Story: [TITLE]\nID: US-[NUMBER]\n")

    lib = TemplateLibrary(template_dir=custom_dir)
    rendered = lib.render_template(TemplateType.USER_STORY, {"TITLE": "Alpha", "NUMBER": "007"})
    assert rendered == "# Custom Story: Alpha\nID: US-007\n"


@pytest.mark.unit
def test_spec_manager_auto_numbers_and_creates_files(tmp_path: Path) -> None:
    """SpecManager.create auto-numbers US-001, US-002 and slugifies filenames."""
    spec_dir = tmp_path / "docs" / "specs"
    mgr = SpecManager(spec_dir=spec_dir, project_root=tmp_path)

    p1 = mgr.create("user-story", title="Biometric Transfer")
    p2 = mgr.create("user-story", title="Instant Refund")
    p3 = mgr.create("scenario", title="Biometric Retry", spec_id="SCENARIO-015")

    assert p1.name == "US-001-biometric-transfer.md"
    assert p2.name == "US-002-instant-refund.md"
    assert p3.name == "SCENARIO-015-biometric-retry.md"
    assert p1.exists() and p2.exists() and p3.exists()


@pytest.mark.unit
def test_spec_manager_export_docs_generates_catalog_with_scores(tmp_path: Path) -> None:
    """SpecManager.export_docs writes a consolidated Markdown catalog with scores and coverage."""
    spec_dir = tmp_path / "docs" / "specs"
    mgr = SpecManager(spec_dir=spec_dir, project_root=tmp_path)
    mgr.create("user-story", title="Checkout Flow", spec_id="US-010")
    mgr.create("api-contract", title="Create Order", spec_id="API-001")

    catalog_path = spec_dir / "CATALOG.md"
    catalog = mgr.export_docs(output_path=catalog_path)

    assert catalog_path.exists()
    assert "# Specification Catalog" in catalog
    assert "US-010-checkout-flow.md" in catalog
    assert "API-001-create-order.md" in catalog
    assert "Orphaned Specifications" in catalog


@pytest.mark.unit
def test_vscode_spec_kit_snippets_valid_json_with_all_5_templates() -> None:
    """Workspace `.vscode/spec-kit.code-snippets` is valid JSON covering all 5 prefixes."""
    snippets_path = REPO_ROOT / ".vscode" / "spec-kit.code-snippets"
    assert snippets_path.exists()
    data = json.loads(snippets_path.read_text(encoding="utf-8"))
    prefixes = {entry["prefix"] for entry in data.values()}
    assert {
        "pvs-user-story",
        "pvs-scenario",
        "pvs-api-contract",
        "pvs-nfr",
        "pvs-decision",
    }.issubset(prefixes)


@pytest.mark.unit
def test_render_template_injects_iso_today_date() -> None:
    """render_template automatically fills [TODAY] with YYYY-MM-DD."""
    lib = TemplateLibrary()
    rendered = lib.render_template(TemplateType.DECISION, {"NUMBER": "005", "TITLE": "Cache"})
    assert "[TODAY]" not in rendered
    assert "DEC-005" in rendered


@pytest.mark.unit
def test_spec_manager_creates_api_contract_and_nfr_with_custom_vars(tmp_path: Path) -> None:
    """SpecManager substitutes HTTP method, API path, and NFR category cleanly."""
    mgr = SpecManager(spec_dir=tmp_path / "specs", project_root=tmp_path)
    api_path = mgr.create(
        "api-contract",
        title="Refund Payment",
        variables={"METHOD": "DELETE", "PATH": "payments/{id}"},
    )
    nfr_path = mgr.create("nfr", title="Encryption Standard", variables={"CATEGORY": "security"})

    api_text = api_path.read_text(encoding="utf-8")
    nfr_text = nfr_path.read_text(encoding="utf-8")
    assert "`DELETE /api/v1/payments/{id}`" in api_text
    assert "**Category**: security" in nfr_text

"""
Integration tests for EVO-020 TraceabilityLinker, DiscoverySpecGenerator, and `pvs spec` CLI.
"""

import json
from pathlib import Path

import pytest

from product_value_system.cli.main import main as cli_main
from product_value_system.spec_kit import (
    DiscoverySpecGenerator,
    SpecManager,
    SpecValidator,
    TraceabilityLinker,
)


def _seed_traceable_project(tmp_path: Path) -> tuple[Path, Path]:
    spec_dir = tmp_path / "docs" / "specs"
    src_dir = tmp_path / "src" / "transfers"
    tests_dir = tmp_path / "tests" / "transfers"
    src_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    mgr = SpecManager(spec_dir=spec_dir, project_root=tmp_path)
    mgr.create("user-story", title="Biometric Transfer", spec_id="US-042")
    mgr.create("scenario", title="Biometric Retry", spec_id="SCENARIO-015")
    mgr.create("user-story", title="Orphaned Planned Feature", spec_id="US-099")

    (src_dir / "biometric.py").write_text(
        "# Implements US-042 and SCENARIO-015\n"
        "def verify_biometric() -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )
    (tests_dir / "test_biometric.py").write_text(
        "# Verifies US-042\n"
        "def test_verify() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path, spec_dir


@pytest.mark.integration
def test_traceability_linker_finds_code_and_test_references(tmp_path: Path) -> None:
    """TraceabilityLinker locates spec ID references across src/ and tests/ with line numbers."""
    root, _ = _seed_traceable_project(tmp_path)
    linker = TraceabilityLinker(project_root=root)

    links = linker.find_spec_references("US-042")
    assert len(links) == 2
    by_type = {lnk.target_type: lnk for lnk in links}
    assert by_type["code"].target_path == "src/transfers/biometric.py"
    assert by_type["code"].line_number == 1
    assert by_type["test"].target_path == "tests/transfers/test_biometric.py"


@pytest.mark.integration
def test_traceability_coverage_report_identifies_orphans_and_percentage(tmp_path: Path) -> None:
    """Coverage report calculates code/test ratios and lists orphaned specs."""
    root, spec_dir = _seed_traceable_project(tmp_path)
    report = TraceabilityLinker(project_root=root).generate_coverage_report(spec_dir)

    assert report["total_specs"] == 3
    assert report["specs_with_code"] == 2  # US-042, SCENARIO-015
    assert report["specs_with_tests"] == 1  # US-042
    assert report["orphaned_specs"] == ["US-099"]
    # (2 + 1) / (3 * 2) = 50.0%
    assert report["coverage_percentage"] == pytest.approx(50.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_discovery_spec_generator_with_llm_and_deterministic_fallback() -> None:
    """DiscoverySpecGenerator produces valid Given/When/Then stories from customer insights."""

    async def _mock_llm(_prompt: str, **_kwargs: object) -> str:
        return json.dumps(
            {
                "TITLE": "Fast Biometric PIX Validation",
                "USER_ROLE": "mobile banking customer",
                "GOAL": "complete biometric transfer verification in under 5 seconds",
                "BENEFIT": "send payments securely without checkout abandonment",
            }
        )

    gen_llm = DiscoverySpecGenerator(llm_callable=_mock_llm)
    story_llm = await gen_llm.generate_user_story_from_insight(
        {
            "pain_point": "Biometric verification takes too long",
            "job_to_be_done": "Complete transfer in <5 seconds",
        },
        story_number="042",
    )
    val_llm = SpecValidator().validate_user_story(story_llm)
    assert val_llm.valid is True
    assert val_llm.score >= 95.0
    assert "Fast Biometric PIX Validation" in story_llm
    assert "mobile banking customer" in story_llm

    # Deterministic fallback without LLM
    gen_det = DiscoverySpecGenerator(llm_callable=None)
    story_det = await gen_det.generate_user_story_from_insight(
        {
            "pain_point": "High retry abandonment",
            "job_to_be_done": "Retry failed payments cleanly",
            "user_role": "merchant",
        },
        story_number="043",
    )
    val_det = SpecValidator().validate_user_story(story_det)
    assert val_det.valid is True
    assert "merchant" in story_det


@pytest.mark.integration
def test_cli_spec_new_and_validate_single_and_directory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs spec new` and `pvs spec validate` work for single files and directories."""
    spec_dir = tmp_path / "docs" / "specs"
    out_file = spec_dir / "US-042-biometric-transfer.md"

    assert (
        cli_main(
            [
                "spec",
                "new",
                "user-story",
                "--title",
                "Biometric Transfer",
                "--id",
                "US-042",
                "--role",
                "customer",
                "--output",
                str(out_file),
            ]
        )
        == 0
    )
    assert out_file.exists()
    capsys.readouterr()

    assert cli_main(["spec", "validate", str(out_file), "--min-score", "90"]) == 0
    assert "PASSED" in capsys.readouterr().out

    # Directory validation catches broken spec
    broken = spec_dir / "US-999-broken.md"
    broken.write_text("# User Story: Broken\n\nNo sections.\n", encoding="utf-8")
    assert cli_main(["spec", "validate", str(spec_dir)]) == 1
    assert "FAIL" in capsys.readouterr().out


@pytest.mark.integration
def test_cli_spec_trace_coverage_and_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs spec trace`, `coverage` (text & JSON), and `export` run end-to-end."""
    root, spec_dir = _seed_traceable_project(tmp_path)

    # trace
    assert cli_main(["spec", "trace", "US-042", "--root", str(root)]) == 0
    trace_out = capsys.readouterr().out
    assert "src/transfers/biometric.py:1" in trace_out
    assert "tests/transfers/test_biometric.py:1" in trace_out

    # coverage (text)
    assert cli_main(["spec", "coverage", str(spec_dir), "--root", str(root)]) == 0
    cov_out = capsys.readouterr().out
    assert "Total specs:       3" in cov_out
    assert "US-099" in cov_out

    # coverage (json)
    assert (
        cli_main(
            ["spec", "coverage", str(spec_dir), "--root", str(root), "--format", "json"]
        )
        == 0
    )
    cov_json = json.loads(capsys.readouterr().out)
    assert cov_json["coverage_percentage"] == pytest.approx(50.0)

    # export
    catalog = tmp_path / "CATALOG.md"
    assert (
        cli_main(
            ["spec", "export", str(spec_dir), "--root", str(root), "--output", str(catalog)]
        )
        == 0
    )
    assert catalog.exists()
    assert "US-042-biometric-transfer.md" in catalog.read_text(encoding="utf-8")


@pytest.mark.integration
def test_cli_spec_validate_missing_path_returns_nonzero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI `pvs spec validate` exits 1 cleanly on missing path."""
    assert cli_main(["spec", "validate", str(tmp_path / "does-not-exist.md")]) == 1
    capsys.readouterr()

"""Unit tests for fail-fast Pydantic v2 YAML configuration contracts (Round 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from product_value_system.contracts import (
    CONFIG_SCHEMA_REGISTRY,
    AnalyticsAdaptersConfigFile,
    BudgetLimitsConfigFile,
    OrchestratorConfigFile,
    SandboxConfigFile,
    expand_env_vars,
    read_secret_from_file,
    validate_all_configs,
    validate_config_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_all_repository_yaml_configs_pass_strict_validation() -> None:
    report = validate_all_configs(REPO_ROOT / "config", expand_env=True)
    assert report.is_valid is True, f"Config validation errors: {report.errors}"
    assert set(report.validated_files) == set(CONFIG_SCHEMA_REGISTRY.keys())
    # Ensure 100% of top-level config/*.yaml files are registered and validated
    on_disk = {p.name for p in (REPO_ROOT / "config").glob("*.yaml")}
    assert on_disk == set(CONFIG_SCHEMA_REGISTRY.keys())


def test_strict_config_rejects_unknown_typo_keys(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "orchestrator.yaml"
    bad_yaml.write_text(
        "orchestrator:\n"
        "  parallel_execution:\n"
        "    enabled: true\n"
        "    max_concurret_agents: 5\n",  # Typo!
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate_config_file(bad_yaml, schema=OrchestratorConfigFile)


def test_strict_config_rejects_out_of_range_and_hierarchy_violations(tmp_path: Path) -> None:
    bad_budget = tmp_path / "budget_limits.yaml"
    bad_budget.write_text(
        "default_limits:\n  per_run: 100.0\n  daily: 50.0\n  monthly: 1000.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="per_run <= daily <= monthly"):
        validate_config_file(bad_budget, schema=BudgetLimitsConfigFile)

    bad_sandbox = tmp_path / "sandbox.yaml"
    bad_sandbox.write_text(
        "sandbox:\n"
        "  network:\n"
        "    filtering_enabled: true\n"
        "    allowlist:\n"
        "      - port: 70000\n"
        "        domain: api.openai.com\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="Port must be 1..65535"):
        validate_config_file(bad_sandbox, schema=SandboxConfigFile)


def test_config_models_are_immutable_frozen() -> None:
    cfg = validate_config_file(REPO_ROOT / "config" / "orchestrator.yaml")
    with pytest.raises(ValidationError):
        setattr(cfg, "orchestrator", None)


def test_expand_env_vars_defaults_overrides_and_file_secrets(tmp_path: Path) -> None:
    secret_file = tmp_path / "db_dsn.txt"
    secret_file.write_text("sqlite:///from_secret_file.db\n", encoding="utf-8")

    payload = {
        "dsn": "${ANALYTICS_DB_DSN:-sqlite:///fallback.db}",
        "endpoint": "${PROM_URL:-http://localhost:9090}",
        "required": "${REQUIRED_SETTING}",
        "nested": ["${LIST_VAR:-default_item}", 42],
    }

    # Non-strict leaves unset placeholders intact and applies defaults
    expanded_default = expand_env_vars(payload, env={})
    assert expanded_default["dsn"] == "sqlite:///fallback.db"
    assert expanded_default["endpoint"] == "http://localhost:9090"
    assert expanded_default["required"] == "${REQUIRED_SETTING}"
    assert expanded_default["nested"] == ["default_item", 42]

    # OWASP *_FILE secret mount overrides default when direct env var is unset
    expanded_file = expand_env_vars(
        payload,
        env={
            "ANALYTICS_DB_DSN_FILE": str(secret_file),
            "REQUIRED_SETTING": "configured-value",
        },
        strict=True,
    )
    assert expanded_file["dsn"] == "sqlite:///from_secret_file.db"
    assert expanded_file["required"] == "configured-value"

    # Strict mode fails fast on missing required var
    with pytest.raises(KeyError, match="REQUIRED_SETTING"):
        expand_env_vars(payload, env={}, strict=True)


def test_read_secret_from_file_validation_and_analytics_expansion(tmp_path: Path) -> None:
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("   \n", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        read_secret_from_file(empty_file)

    with pytest.raises(FileNotFoundError):
        read_secret_from_file(tmp_path / "does_not_exist.txt")

    analytics_cfg = validate_config_file(
        REPO_ROOT / "config" / "analytics_adapters.yaml",
        schema=AnalyticsAdaptersConfigFile,
        expand_env=True,
        env={"PROMETHEUS_URL": "http://prom.internal:9090"},
    )
    assert isinstance(analytics_cfg, AnalyticsAdaptersConfigFile)
    assert analytics_cfg.adapters.prometheus is not None
    assert analytics_cfg.adapters.prometheus.base_url == "http://prom.internal:9090"
    assert analytics_cfg.adapters.sql is not None
    assert analytics_cfg.adapters.sql.connection_string == "sqlite:///analytics.db"

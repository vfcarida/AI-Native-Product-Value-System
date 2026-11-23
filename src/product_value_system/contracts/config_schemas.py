"""
Fail-fast Pydantic v2 configuration schemas for all ``config/*.yaml`` files (Round 3).

Uses ``ConfigDict(extra="forbid", frozen=True, validate_default=True)`` recursively
so unknown keys, typos, and out-of-range thresholds fail fast at startup rather than
silently degrading in production.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def read_secret_from_file(secret_path: Path | str) -> str:
    """Read a secret from a mounted file per OWASP secrets management guidance.

    Raises ``FileNotFoundError`` if the file does not exist and ``ValueError``
    if the file is empty or whitespace-only.
    """
    path = Path(secret_path)
    if not path.is_file():
        raise FileNotFoundError(f"Secret file not found: {path}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Secret file is empty: {path}")
    return value


def expand_env_vars(
    value: Any,
    env: Mapping[str, str] | None = None,
    *,
    strict: bool = False,
) -> Any:
    """Recursively expand ``${VAR}`` and ``${VAR:-default}`` placeholders safely.

    Follows 12-Factor App Factor III and OWASP secrets management best practices:
    - Never invokes ``eval`` or unsafe YAML constructors.
    - Supports Kubernetes/Docker secret-file convention: if ``VAR`` is unset but
      ``VAR_FILE`` points to a file, reads the value via ``read_secret_from_file``.
    - When ``strict=False`` (default), unresolved ``${VAR}`` without a default is
      preserved verbatim so offline config schema validation works without live keys.
    - When ``strict=True``, unresolved ``${VAR}`` raises ``KeyError`` (fail-fast).
    """
    environ: Mapping[str, str] = os.environ if env is None else env

    if isinstance(value, str):
        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default_val = match.group(2)
            if var_name in environ and environ[var_name] != "":
                return environ[var_name]
            file_var = f"{var_name}_FILE"
            if file_var in environ and environ[file_var] != "":
                return read_secret_from_file(environ[file_var])
            if default_val is not None:
                return default_val
            if strict:
                raise KeyError(f"Required environment variable '{var_name}' is not set")
            return match.group(0)

        return _ENV_VAR_PATTERN.sub(_replace, value)
    if isinstance(value, list):
        return [expand_env_vars(item, environ, strict=strict) for item in value]
    if isinstance(value, dict):
        return {k: expand_env_vars(v, environ, strict=strict) for k, v in value.items()}
    return value


class StrictConfigModel(BaseModel):
    """Immutable, extra-forbidding base model for all YAML configuration contracts."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )


# ---------------------------------------------------------------------------
# 1. config/orchestrator.yaml
# ---------------------------------------------------------------------------


class ParallelExecutionSchema(StrictConfigModel):
    enabled: bool = True
    max_concurrent_agents: int = Field(default=5, ge=1, le=100)
    max_concurrent_llm_calls: int = Field(default=10, ge=1, le=500)
    llm_rate_limit_per_minute: int = Field(default=100, ge=1)
    memory_limit_mb: int = Field(default=2000, ge=64)
    conflict_strategy: Literal["merge", "priority", "manual"] = "merge"
    fallback_to_sequential: bool = True


class OrchestratorBlockSchema(StrictConfigModel):
    parallel_execution: ParallelExecutionSchema = Field(default_factory=ParallelExecutionSchema)


class OrchestratorConfigFile(StrictConfigModel):
    orchestrator: OrchestratorBlockSchema


# ---------------------------------------------------------------------------
# 2. config/caching.yaml
# ---------------------------------------------------------------------------


class RedisCacheSchema(StrictConfigModel):
    host: str = "localhost"
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0)
    password: str | None = None


class SQLiteCacheSchema(StrictConfigModel):
    path: str = ".cache/llm_cache.db"


class CachingBlockSchema(StrictConfigModel):
    enabled: bool = True
    backend: Literal["memory", "sqlite", "redis", "tiered"] = "memory"
    redis: RedisCacheSchema = Field(default_factory=RedisCacheSchema)
    sqlite: SQLiteCacheSchema = Field(default_factory=SQLiteCacheSchema)
    ttl_seconds: int = Field(default=604800, ge=1)
    enable_semantic_matching: bool = True
    similarity_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    embedding_model: str = "text-embedding-3-small"
    max_entries: int = Field(default=10000, ge=1)
    max_size_mb: int = Field(default=1000, ge=1)
    invalidate_on_model_change: bool = True


class CachingConfigFile(StrictConfigModel):
    caching: CachingBlockSchema


# ---------------------------------------------------------------------------
# 3. config/routing.yaml
# ---------------------------------------------------------------------------


class RoutedModelEntrySchema(StrictConfigModel):
    provider: Literal["openai", "anthropic", "gemini", "azure_openai", "ollama"]
    model: str = Field(min_length=1)
    cost_per_1m_input: float = Field(ge=0.0)
    cost_per_1m_output: float = Field(ge=0.0)


class RoutingModelsSchema(StrictConfigModel):
    low_complexity: list[RoutedModelEntrySchema] = Field(min_length=1)
    medium_complexity: list[RoutedModelEntrySchema] = Field(min_length=1)
    high_complexity: list[RoutedModelEntrySchema] = Field(min_length=1)


class RoutingQualityCheckSchema(StrictConfigModel):
    enabled: bool = True
    fallback_on_failure: bool = True
    validation_method: Literal["schema_compliance", "llm_judge"] = "schema_compliance"


class RoutingCostTrackingSchema(StrictConfigModel):
    enabled: bool = True
    alert_threshold_usd: float = Field(default=100.0, gt=0.0)


class RoutingBlockSchema(StrictConfigModel):
    enabled: bool = True
    shadow_mode: bool = False
    force_tier: Literal["low", "medium", "high"] | None = None
    preferred_provider: (
        Literal["openai", "anthropic", "gemini", "azure_openai", "ollama"] | None
    ) = None
    models: RoutingModelsSchema
    complexity_thresholds: dict[str, dict[str, Any]] = Field(default_factory=dict)
    quality_check: RoutingQualityCheckSchema = Field(default_factory=RoutingQualityCheckSchema)
    cost_tracking: RoutingCostTrackingSchema = Field(default_factory=RoutingCostTrackingSchema)


class RoutingConfigFile(StrictConfigModel):
    routing: RoutingBlockSchema


# ---------------------------------------------------------------------------
# 4. config/evaluation.yaml
# ---------------------------------------------------------------------------


class BatchModeSchema(StrictConfigModel):
    enabled: bool = True
    provider: Literal["openai", "anthropic", "gemini", "azure_openai"] = "openai"
    batch_size: int = Field(default=50, ge=1, le=50000)
    max_concurrent_batches: int = Field(default=10, ge=1, le=100)
    poll_interval_seconds: float = Field(default=60.0, gt=0.0)
    max_poll_duration_seconds: float = Field(default=86400.0, gt=0.0)
    fallback_to_realtime: bool = True
    realtime_threshold_samples: int = Field(default=10, ge=0)
    track_savings: bool = True


class EvaluationBlockSchema(StrictConfigModel):
    batch_mode: BatchModeSchema = Field(default_factory=BatchModeSchema)


class EvaluationConfigFile(StrictConfigModel):
    evaluation: EvaluationBlockSchema


# ---------------------------------------------------------------------------
# 5. config/sandbox.yaml
# ---------------------------------------------------------------------------


class SandboxAllowlistEntrySchema(StrictConfigModel):
    domain: str | None = None
    ip: str | None = None
    port: int | Literal["any"] = 443
    protocol: Literal["tcp", "udp", "icmp", "any"] = "tcp"

    @model_validator(mode="after")
    def _validate_target_and_port(self) -> SandboxAllowlistEntrySchema:
        if not self.domain and not self.ip:
            raise ValueError("Allowlist entry must set 'domain' or 'ip'")
        if isinstance(self.port, int) and not (1 <= self.port <= 65535):
            raise ValueError(f"Port must be 1..65535, got {self.port}")
        return self


class SandboxNetworkSchema(StrictConfigModel):
    filtering_enabled: bool = True
    mode: Literal["allowlist", "denylist"] = "allowlist"
    dns_cache_ttl_seconds: int = Field(default=300, ge=1)
    dns_resolve_retries: int = Field(default=2, ge=1, le=10)
    allowlist: list[SandboxAllowlistEntrySchema] = Field(default_factory=list)
    log_blocked_connections: bool = True
    log_path: str = "/var/log/sandbox_network_blocks.log"
    fallback_on_error: bool = True


class SandboxBlockSchema(StrictConfigModel):
    network: SandboxNetworkSchema = Field(default_factory=SandboxNetworkSchema)


class SandboxConfigFile(StrictConfigModel):
    sandbox: SandboxBlockSchema


# ---------------------------------------------------------------------------
# 6. config/brownfield.yaml
# ---------------------------------------------------------------------------


class GitAnalysisSchema(StrictConfigModel):
    enabled: bool = True
    lookback_days: int = Field(default=180, ge=1)
    min_commits_for_hotspot: int = Field(default=10, ge=1)
    coupling_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_co_changes: int = Field(default=2, ge=1)
    max_files_per_commit: int = Field(default=50, ge=2)
    silo_threshold_pct: float = Field(default=80.0, ge=0.0, le=100.0)
    anonymize_authors: bool = True
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    max_commits_analyzed: int = Field(default=10000, ge=1)


class BoundaryDetectionSchema(StrictConfigModel):
    method: Literal["static_analysis", "dependency_graph", "ml"] = "static_analysis"
    min_module_size_lines: int = Field(default=500, ge=0)
    max_module_size_lines: int = Field(default=10000, ge=1)

    @model_validator(mode="after")
    def _validate_size_bounds(self) -> BoundaryDetectionSchema:
        if self.min_module_size_lines > self.max_module_size_lines:
            raise ValueError("min_module_size_lines cannot exceed max_module_size_lines")
        return self


class StranglerRoutingSchema(StrictConfigModel):
    strategy: Literal["feature_flag", "reverse_proxy", "api_gateway"] = "feature_flag"
    initial_percentage: int = Field(default=1, ge=0, le=100)
    stages: list[int] = Field(default_factory=lambda: [1, 5, 25, 50, 100])
    monitoring_duration_per_stage_hours: int = Field(default=24, ge=1)

    @model_validator(mode="after")
    def _validate_stages(self) -> StranglerRoutingSchema:
        if any(not (0 <= s <= 100) for s in self.stages):
            raise ValueError("All rollout stage percentages must be in [0, 100]")
        if self.stages != sorted(self.stages):
            raise ValueError("Rollout stages must be in ascending order")
        return self


class StranglerFigSchema(StrictConfigModel):
    enabled: bool = True
    top_candidates: int = Field(default=3, ge=1)
    boundary_detection: BoundaryDetectionSchema = Field(default_factory=BoundaryDetectionSchema)
    priority_factors: list[dict[str, float]] | dict[str, float] = Field(default_factory=dict)
    routing: StranglerRoutingSchema = Field(default_factory=StranglerRoutingSchema)


class BrownfieldBlockSchema(StrictConfigModel):
    git_analysis: GitAnalysisSchema = Field(default_factory=GitAnalysisSchema)
    strangler_fig: StranglerFigSchema = Field(default_factory=StranglerFigSchema)


class BrownfieldConfigFile(StrictConfigModel):
    brownfield: BrownfieldBlockSchema


# ---------------------------------------------------------------------------
# 7. config/metrics.yaml
# ---------------------------------------------------------------------------


class CycleTimeStageSchema(StrictConfigModel):
    name: str = Field(min_length=1)
    start_event: str = Field(min_length=1)
    end_event: str = Field(min_length=1)


class CycleTimeBlockSchema(StrictConfigModel):
    stages: list[CycleTimeStageSchema] = Field(default_factory=list)


class SurveyQuestionSchema(StrictConfigModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    type: Literal["rating", "numeric", "free_text"] = "rating"


class SurveysBlockSchema(StrictConfigModel):
    enabled: bool = True
    frequency_days: int = Field(default=14, ge=1)
    questions: list[SurveyQuestionSchema] = Field(default_factory=list)


class AIAssistBlockSchema(StrictConfigModel):
    track_usage: bool = True
    track_acceptance: bool = True
    track_corrections: bool = True


class DevXBlockSchema(StrictConfigModel):
    enabled: bool = True
    dimensions: dict[str, bool] = Field(default_factory=dict)
    cycle_time: CycleTimeBlockSchema = Field(default_factory=CycleTimeBlockSchema)
    surveys: SurveysBlockSchema = Field(default_factory=SurveysBlockSchema)
    ai_assist: AIAssistBlockSchema = Field(default_factory=AIAssistBlockSchema)
    anonymize: bool = True
    aggregate_only: bool = True
    retention_days: int = Field(default=90, ge=1)


class MetricsConfigFile(StrictConfigModel):
    devx: DevXBlockSchema


# ---------------------------------------------------------------------------
# 8. config/budget_limits.yaml
# ---------------------------------------------------------------------------


class BudgetDefaultLimitsSchema(StrictConfigModel):
    per_run: float = Field(gt=0.0)
    daily: float = Field(gt=0.0)
    monthly: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _validate_hierarchy(self) -> BudgetDefaultLimitsSchema:
        if self.per_run > self.daily or self.daily > self.monthly:
            raise ValueError("Budget limits must satisfy per_run <= daily <= monthly")
        return self


class BudgetAlertsSchema(StrictConfigModel):
    threshold: float = Field(default=0.8, gt=0.0, le=1.0)


class EmergencyStopSchema(StrictConfigModel):
    enabled: bool = True
    env_var: str = Field(default="EMERGENCY_STOP", min_length=1)


class BudgetLimitsConfigFile(StrictConfigModel):
    default_limits: BudgetDefaultLimitsSchema
    alerts: BudgetAlertsSchema = Field(default_factory=BudgetAlertsSchema)
    emergency_stop: EmergencyStopSchema = Field(default_factory=EmergencyStopSchema)


# ---------------------------------------------------------------------------
# 9. config/secret_detection.yaml
# ---------------------------------------------------------------------------


class SecretDetectionBlockSchema(StrictConfigModel):
    enabled: bool = True
    use_ml: bool = True
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    entropy_threshold: float = Field(default=4.2, ge=1.0, le=8.0)
    allowlist_file: str = "config/secret_detection_allowlist.yaml"
    entity_types: list[str] = Field(default_factory=list)


class SecretDetectionConfigFile(StrictConfigModel):
    secret_detection: SecretDetectionBlockSchema


# ---------------------------------------------------------------------------
# 10. config/model_adapters.yaml
# ---------------------------------------------------------------------------


class RemoteModelAdapterSchema(StrictConfigModel):
    api_key: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    model: str = Field(min_length=1)
    timeout: float = Field(default=60.0, gt=0.0)
    max_retries: int = Field(default=3, ge=0, le=20)


class MockModelAdapterSchema(StrictConfigModel):
    default_response: str = "Mock response"
    simulate_latency_ms: int = Field(default=0, ge=0)


class ModelAdaptersMapSchema(StrictConfigModel):
    openai: RemoteModelAdapterSchema | None = None
    anthropic: RemoteModelAdapterSchema | None = None
    mock: MockModelAdapterSchema = Field(default_factory=MockModelAdapterSchema)


class ModelAdaptersConfigFile(StrictConfigModel):
    default_adapter: Literal["openai", "anthropic", "mock"] = "mock"
    adapters: ModelAdaptersMapSchema


# ---------------------------------------------------------------------------
# 11. config/analytics_adapters.yaml
# ---------------------------------------------------------------------------


class SQLAnalyticsAdapterSchema(StrictConfigModel):
    type: Literal["sql"] = "sql"
    connection_string: str = Field(min_length=1)
    table: str = Field(default="metrics", min_length=1)
    expected_columns: dict[str, str] = Field(default_factory=dict)


class PrometheusAnalyticsAdapterSchema(StrictConfigModel):
    type: Literal["prometheus"] = "prometheus"
    base_url: str = Field(min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0.0)


class MockAnalyticsAdapterSchema(StrictConfigModel):
    type: Literal["mock"] = "mock"
    sample_size: int = Field(default=1000, ge=1)
    ci_half_width_ratio: float = Field(default=0.05, gt=0.0, lt=1.0)
    seed: int = 42
    metrics: dict[str, float] = Field(default_factory=dict)


class AnalyticsAdaptersMapSchema(StrictConfigModel):
    sql: SQLAnalyticsAdapterSchema | None = None
    prometheus: PrometheusAnalyticsAdapterSchema | None = None
    mock: MockAnalyticsAdapterSchema = Field(default_factory=MockAnalyticsAdapterSchema)


class AnalyticsAlertsSchema(StrictConfigModel):
    on_regression: Literal["info", "warning", "critical"] = "critical"
    on_guardrail_breach: Literal["info", "warning", "critical"] = "critical"
    on_target_missed: Literal["info", "warning", "critical"] = "warning"
    include_info_alerts: bool = False


class AnalyticsMeasurementSchema(StrictConfigModel):
    default_measurement_window_days: int = Field(default=30, ge=1)
    default_baseline_window_days: int = Field(default=30, ge=1)
    significance_alpha: float = Field(default=0.05, gt=0.0, lt=1.0)
    feedback_store_path: str = Field(min_length=1)
    alerts: AnalyticsAlertsSchema = Field(default_factory=AnalyticsAlertsSchema)


class AnalyticsAdaptersConfigFile(StrictConfigModel):
    default_adapter: Literal["sql", "prometheus", "mock"] = "mock"
    adapters: AnalyticsAdaptersMapSchema
    measurement: AnalyticsMeasurementSchema


# ---------------------------------------------------------------------------
# 12. config/secret_detection_allowlist.yaml
# ---------------------------------------------------------------------------


class SecretAllowlistEntrySchema(StrictConfigModel):
    pattern: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class SecretDetectionAllowlistConfigFile(StrictConfigModel):
    allowlist: list[SecretAllowlistEntrySchema] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 13. config/network_allowlist.yaml (PROD-003)
# ---------------------------------------------------------------------------


class NetworkAllowlistRuleSchema(StrictConfigModel):
    destination: str = Field(min_length=1)
    port: int | Literal["any"] = 443
    protocol: Literal["tcp", "udp", "icmp", "any"] = "tcp"
    description: str = ""

    @model_validator(mode="after")
    def _validate_port(self) -> NetworkAllowlistRuleSchema:
        if isinstance(self.port, int) and not (1 <= self.port <= 65535):
            raise ValueError(f"Port must be 1..65535, got {self.port}")
        return self


class NetworkAllowlistConfigFile(StrictConfigModel):
    version: str = "1.0"
    description: str = ""
    allowlist: list[NetworkAllowlistRuleSchema] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Registry & Unified Validator
# ---------------------------------------------------------------------------

CONFIG_SCHEMA_REGISTRY: dict[str, type[StrictConfigModel]] = {
    "orchestrator.yaml": OrchestratorConfigFile,
    "caching.yaml": CachingConfigFile,
    "routing.yaml": RoutingConfigFile,
    "routing_multiprovider.yaml": RoutingConfigFile,
    "evaluation.yaml": EvaluationConfigFile,
    "sandbox.yaml": SandboxConfigFile,
    "network_allowlist.yaml": NetworkAllowlistConfigFile,
    "brownfield.yaml": BrownfieldConfigFile,
    "metrics.yaml": MetricsConfigFile,
    "budget_limits.yaml": BudgetLimitsConfigFile,
    "secret_detection.yaml": SecretDetectionConfigFile,
    "model_adapters.yaml": ModelAdaptersConfigFile,
    "analytics_adapters.yaml": AnalyticsAdaptersConfigFile,
    "secret_detection_allowlist.yaml": SecretDetectionAllowlistConfigFile,
}


@dataclass
class ConfigValidationReport:
    """Aggregate report from validating all repository YAML config contracts."""

    validated_files: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "validated_files": list(self.validated_files),
            "errors": dict(self.errors),
        }


def validate_config_file(
    path: Path | str,
    schema: type[StrictConfigModel] | None = None,
    *,
    expand_env: bool = False,
    strict_env: bool = False,
    env: Mapping[str, str] | None = None,
) -> StrictConfigModel:
    """Parse and strictly validate a single YAML configuration file."""
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {cfg_path}")

    target_schema = schema or CONFIG_SCHEMA_REGISTRY.get(cfg_path.name)
    if target_schema is None:
        raise KeyError(f"No configuration schema registered for {cfg_path.name!r}")

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected YAML mapping in {cfg_path}, got {type(raw).__name__}")

    if expand_env:
        raw = expand_env_vars(raw, env=env, strict=strict_env)

    return target_schema.model_validate(raw)


def validate_all_configs(
    config_dir: Path | str = Path("config"),
    *,
    expand_env: bool = False,
) -> ConfigValidationReport:
    """Validate every registered YAML config present in ``config_dir``."""
    base = Path(config_dir)
    report = ConfigValidationReport()
    for filename, schema in sorted(CONFIG_SCHEMA_REGISTRY.items()):
        cfg_path = base / filename
        if not cfg_path.is_file():
            report.errors[filename] = f"Missing required config file: {cfg_path}"
            continue
        try:
            validate_config_file(cfg_path, schema=schema, expand_env=expand_env)
            report.validated_files.append(filename)
        except (ValidationError, ValueError, KeyError, yaml.YAMLError, OSError) as exc:
            report.errors[filename] = str(exc)
    return report

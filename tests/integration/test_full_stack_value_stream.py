"""Full-stack cross-cutting golden-path integration test (Round 8).

Exercises the integrated AI-Native Product Value System end-to-end at zero API cost:
  1. Fail-fast strict config validation across all 12 config/*.yaml contracts
  2. FinOps stack: Complexity Model Routing + Semantic Prompt Caching
  3. Resilience stack: CircuitBreaker + Full-Jitter Retry recovering from transient 429
  4. Governance & Security: Cedar PolicyEngine + Hybrid ML Secret Scan on all artifacts
  5. Parallel Orchestrator: DAG parallel execution of review stages + Checkpointing
  6. Unified Observability: Prometheus /metrics (pvs_* + devx_*), /health/ready, /devx SPACE
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from prometheus_client import CollectorRegistry

from product_value_system.adapters.model_adapter import Message, ModelResponse, Role, Usage
from product_value_system.agents.agent import AgentResult
from product_value_system.api.health import create_app
from product_value_system.caching import (
    MemoryCacheBackend,
    PromptCache,
    deserialize_model_response,
    serialize_model_response,
)
from product_value_system.contracts import validate_all_configs
from product_value_system.harness.resilience import CircuitBreaker, execute_with_resilience
from product_value_system.metrics import DevXTracker
from product_value_system.monitoring.metrics import PVSMetrics
from product_value_system.orchestrator import (
    CheckpointManager,
    RunStatus,
    ValueStreamOrchestrator,
)
from product_value_system.policy.engine import PolicyEngine
from product_value_system.routing import ModelRouter
from product_value_system.security.secret_detector import HybridSecretDetector

REPO_ROOT = Path(__file__).resolve().parents[2]


class _FullStackStageAgent:
    """Deterministic stage agent backed by ModelRouter + PromptCache + CircuitBreaker."""

    def __init__(
        self,
        name: str,
        task_type: str,
        payload: dict[str, Any],
        router: ModelRouter,
        cache: PromptCache,
        breaker: CircuitBreaker,
        *,
        fail_once: bool = False,
        delay: float = 0.02,
    ) -> None:
        self.name = name
        self.task_type = task_type
        self.payload = payload
        self.router = router
        self.cache = cache
        self.breaker = breaker
        self._fail_once = fail_once
        self._attempts = 0
        self.delay = delay
        self.last_model: str | None = None

    async def execute(
        self,
        input_artifact: dict[str, Any],
        _harness: Any,
        _context: Any,
    ) -> AgentResult:
        prompt = f"Stage={self.name}; problem={input_artifact.get('id', 'p-1')}"
        messages = [Message(role=Role.USER, content=prompt)]
        decision = self.router.route(messages, task_type=self.task_type)
        self.last_model = decision.model

        async def _provider_call() -> ModelResponse:
            self._attempts += 1
            if self._fail_once and self._attempts == 1:
                raise RuntimeError("HTTP 429 rate limit exceeded")
            await asyncio.sleep(self.delay)
            return ModelResponse(
                content=f"ok:{self.name}:{decision.model}",
                model=decision.model,
                usage=Usage(input_tokens=120, output_tokens=60),
                finish_reason="stop",
            )

        async def _resilient_call() -> ModelResponse:
            return await execute_with_resilience(
                _provider_call,
                circuit_breaker=self.breaker,
                max_attempts=3,
                base_delay_seconds=0.001,
                max_delay_seconds=0.005,
            )

        cached = await self.cache.get(messages, decision.model, 0.0, 512)
        if cached is None:
            response = await _resilient_call()
            await self.cache.set(
                messages, decision.model, 0.0, 512, serialize_model_response(response)
            )
        else:
            response = deserialize_model_response(cached)

        # Second identical lookup proves transparent PromptCache hit
        hit_payload = await self.cache.get(messages, decision.model, 0.0, 512)
        assert hit_payload is not None
        assert deserialize_model_response(hit_payload).content == response.content

        artifact = dict(self.payload)
        artifact["routed_model"] = decision.model
        return AgentResult(
            success=True,
            output_artifact=artifact,
            cost_usd=0.004,
            iterations=1,
            duration_seconds=self.delay,
            reasoning_trace=[],
            tool_calls=[],
            checkpoints=[],
        )


async def test_full_stack_value_stream_end_to_end(tmp_path: Path) -> None:
    """Golden-path E2E test validating all subsystems working seamlessly together."""
    # 1. Fail-fast config validation gate
    cfg_report = validate_all_configs(REPO_ROOT / "config", expand_env=True)
    assert cfg_report.is_valid is True, f"Config errors: {cfg_report.errors}"
    assert len(cfg_report.validated_files) >= 12

    # 2. Initialize FinOps (Routing + Cache), Resilience, Policy, and Telemetry
    router = ModelRouter.from_yaml(REPO_ROOT / "config" / "routing.yaml")
    cache = PromptCache(backend=MemoryCacheBackend(max_entries=100))
    breaker = CircuitBreaker(name="e2e-llm", failure_threshold=5, recovery_timeout_seconds=10.0)
    policy_engine = PolicyEngine(policies_dir=REPO_ROOT / "policies")

    pvs_metrics = PVSMetrics(register_global_prometheus=False)
    devx_reg = CollectorRegistry()
    devx_tracker = DevXTracker.from_yaml(REPO_ROOT / "config" / "metrics.yaml", registry=devx_reg)

    agents = {
        "discovery": _FullStackStageAgent(
            "discovery-agent",
            "ideation",
            {
                "id": "outcome-e2e-1",
                "hypothesis": "Automated full-stack orchestration cuts lead time by 40%.",
                "success_criteria": {"primary_metric": {"name": "lead_time", "unit": "hours"}},
            },
            router,
            cache,
            breaker,
            fail_once=True,  # Exercises transient 429 retry recovery
        ),
        "specification": _FullStackStageAgent(
            "specification-agent",
            "spec_generation",
            {
                "id": "spec-e2e-1",
                "functional_requirements": [{"id": "FR-1", "acceptance_criteria": ["passes"]}],
            },
            router,
            cache,
            breaker,
        ),
        "review_architecture": _FullStackStageAgent(
            "architecture-review-agent",
            "code_review",
            {"id": "rev-arch-1", "verdict": "approve", "findings": []},
            router,
            cache,
            breaker,
            delay=0.05,
        ),
        "review_security": _FullStackStageAgent(
            "security-review-agent",
            "code_review",
            {"id": "rev-sec-1", "verdict": "approve", "findings": []},
            router,
            cache,
            breaker,
            delay=0.05,
        ),
        "build": _FullStackStageAgent(
            "build-agent",
            "format_conversion",
            {"id": "build-1", "status": "success", "verdict": "pass"},
            router,
            cache,
            breaker,
        ),
    }

    # 3. Execute full parallel Value Stream Run
    orchestrator = ValueStreamOrchestrator(
        agents=agents,
        evaluation_pipeline=None,
        checkpoint_manager=CheckpointManager(tmp_path / "checkpoints"),
        artifact_root=tmp_path / "artifacts",
        policy_engine=policy_engine,
        devx_tracker=devx_tracker,
    )

    problem = {
        "id": "prob-e2e-fullstack",
        "title": "Unified AI-Native Checkout Modernization",
        "description": "Deliver checkout API with zero regressions and verified SLOs.",
    }
    run = await orchestrator.execute_run(
        problem,
        config={"enable_parallel_execution": True, "user_id": "eng-fullstack@example.org"},
    )

    assert run.status == RunStatus.COMPLETED
    assert run.total_cost_usd > 0.0
    assert {"problem_analysis", "outcome_hypothesis", "specification", "build"}.issubset(
        run.stages.keys()
    )
    assert {"review_architecture", "review_security"}.issubset(run.stages.keys())

    # Verify FinOps cache hits and complexity-based tier differentiation
    cache_stats = cache.get_stats()
    assert cache_stats["hits"] >= 5
    assert agents["discovery"].last_model != agents["build"].last_model

    # Verify circuit breaker stayed healthy despite transient 429 recovery
    assert breaker.get_stats().state == "closed"

    # 4. Security scan on every generated artifact confirms zero leaked secrets
    detector = HybridSecretDetector.from_config(REPO_ROOT / "config" / "secret_detection.yaml")
    artifact_files = list((tmp_path / "artifacts").rglob("*.yaml"))
    assert len(artifact_files) >= 5
    for artifact_file in artifact_files:
        text = artifact_file.read_text(encoding="utf-8")
        assert detector.detect(text) == [], f"Secret leaked in {artifact_file}"

    # 5. Unified HTTP observability endpoints (/health/ready, /metrics, /devx)
    pvs_metrics.record_request(agent="discovery", status="success", latency_seconds=0.05)
    app = create_app(metrics=pvs_metrics, metrics_api_key="", devx_tracker=devx_tracker)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"

        metrics_resp = await client.get("/metrics")
        assert metrics_resp.status_code == 200
        assert "pvs_requests_total" in metrics_resp.text
        assert "devx_tasks_completed_total" in metrics_resp.text
        assert "devx_ai_usage_total" in metrics_resp.text

        devx_resp = await client.get("/devx")
        assert devx_resp.status_code == 200
        devx_body = devx_resp.json()
        assert devx_body["aggregate_only"] is True
        assert devx_body["ai_effectiveness"]["acceptance_rate"] == 1.0
        assert 0.0 <= devx_body["space_scores"]["overall"] <= 1.0

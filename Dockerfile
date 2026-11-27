# syntax=docker/dockerfile:1
# Multi-target hardened Docker image for AI-Native Product Value System (EVO-P3-03)
# Targets:
#   - runtime (default): Lean non-root production image (no pytest/mypy/ruff) serving Control Plane API
#   - ci-dev:            Extended test/developer image including [dev] tooling and tests/

# ==========================================
# Stage 1: Production Dependency Builder
# ==========================================
FROM python:3.12-slim AS builder-prod

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir ".[monitoring,routing,caching,devx,postgres,vault]"

# ==========================================
# Stage 2: Lean Non-Root Production Runtime (default target)
# ==========================================
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system --gid 10001 pvs && \
    useradd --system --uid 10001 --gid pvs --create-home --shell /bin/bash pvs

WORKDIR /app

COPY --chown=pvs:pvs --from=builder-prod /opt/venv /opt/venv
COPY --chown=pvs:pvs pyproject.toml README.md alembic.ini ./
COPY --chown=pvs:pvs src/ ./src/
COPY --chown=pvs:pvs config/ ./config/
COPY --chown=pvs:pvs schemas/ ./schemas/
COPY --chown=pvs:pvs policies/ ./policies/
COPY --chown=pvs:pvs prompts/ ./prompts/
COPY --chown=pvs:pvs migrations/ ./migrations/
COPY --chown=pvs:pvs alembic/ ./alembic/

USER pvs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=5)"]

CMD ["uvicorn", "product_value_system.api.app_factory:app", "--host", "0.0.0.0", "--port", "8000"]

# ==========================================
# Stage 3: CI / Developer Image (optional --target ci-dev)
# ==========================================
FROM runtime AS ci-dev

USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends make && \
    rm -rf /var/lib/apt/lists/* && \
    /opt/venv/bin/pip install --no-cache-dir ".[dev]"

COPY --chown=pvs:pvs tests/ ./tests/
COPY --chown=pvs:pvs examples/ ./examples/
COPY --chown=pvs:pvs scripts/ ./scripts/
COPY --chown=pvs:pvs Makefile pytest.ini ./

USER pvs
CMD ["pvs", "doctor", "--repo-root", "/app"]

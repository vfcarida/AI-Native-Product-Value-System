# syntax=docker/dockerfile:1
# Multi-stage hardened Docker image for AI-Native Product Value System (Round 14)

# ==========================================
# Stage 1: Builder (isolated virtualenv)
# ==========================================
FROM python:3.15-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir ".[dev,monitoring,routing,caching,devx]"

# ==========================================
# Stage 2: Runtime (non-root hardened)
# ==========================================
FROM python:3.15-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Install minimal runtime tools (git for brownfield analysis, make for CLI workflows)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git make && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --system --gid 10001 pvs && \
    useradd --system --uid 10001 --gid pvs --create-home --shell /bin/bash pvs

WORKDIR /app

COPY --chown=pvs:pvs --from=builder /opt/venv /opt/venv
COPY --chown=pvs:pvs . .

USER pvs

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["pvs", "doctor", "--repo-root", "/app", "--json"]

CMD ["pvs", "doctor", "--repo-root", "/app"]

# Dockerfile for development environment
FROM python:3.12-slim

# Security: Run as non-root
RUN useradd -m -u 1000 -s /bin/bash pvs

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        make \
        && \
    rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY pyproject.toml ./
COPY README.md ./

# Copy source code so editable install succeeds
COPY --chown=pvs:pvs . .

# Install Python dependencies
RUN pip install --no-cache-dir -e ".[dev]"

# Switch to non-root user
USER pvs

# Default command
CMD ["bash"]

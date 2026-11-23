# Agent sandbox: Isolated execution environment for agents
FROM python:3.12-slim

# Install essential tools and iptables for network egress filtering (PROD-003)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        make \
        curl \
        iptables \
        && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (UID 1000)
RUN useradd -m -u 1000 -s /bin/bash pvs

# Set up workspace
RUN mkdir -p /workspace && \
    chown -R pvs:pvs /workspace

# Install Python packages for agent execution
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm /tmp/requirements.txt

# Switch to non-root user
USER pvs
WORKDIR /workspace

# Default command
CMD ["/bin/bash"]

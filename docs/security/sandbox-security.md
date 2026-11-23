# Sandbox Security Architecture (iptables Network Egress Filtering)

## Overview

Docker sandbox containers enforce **default-deny network egress filtering** using `iptables` (`NetworkFilter` + `DockerSandbox` / `DockerSandboxProvider`) to mitigate:

- **Data exfiltration** to unauthorized external domains or IP addresses
- **Command-and-control (C&C)** callbacks from compromised generated code
- **Lateral movement** to internal private RFC1918/metadata subnets
- **Sandbox abuse** as an outbound proxy or pivot host

All sandbox images ([`deploy/docker/Dockerfile.sandbox-python`](../../deploy/docker/Dockerfile.sandbox-python), [`deploy/docker/Dockerfile.sandbox-typescript`](../../deploy/docker/Dockerfile.sandbox-typescript), and [`docker/agent-sandbox/Dockerfile`](../../docker/agent-sandbox/Dockerfile)) install `iptables` and execute user workloads as a non-root UID `1000` user with `cap_drop=["ALL"]` and `no-new-privileges`.

---

## Technical Implementation

At container startup, `NetworkFilter.generate_iptables_rules()` builds a deterministic, injection-validated `OUTPUT` chain executed as root (`user="0"`, `cap_add=["NET_ADMIN"]`) before non-root agent code runs:

1. **Flush `OUTPUT` chain**: `iptables -F OUTPUT` (clean, idempotent slate)
2. **Default-deny egress policy**: `iptables -P OUTPUT DROP`
3. **Permit loopback**: `iptables -A OUTPUT -o lo -j ACCEPT`
4. **Permit return traffic**: `iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT`
5. **Permit DNS resolution**: UDP & TCP port `53` (`-p udp --dport 53`, `-p tcp --dport 53`)
6. **Permit allowlisted destinations**: Direct IPv4/CIDR rules and concurrently resolved IPv4 addresses from [`config/network_allowlist.yaml`](../../config/network_allowlist.yaml) (1-hour DNS TTL cache)
7. **Audit-log denied egress**: `iptables -A OUTPUT -j LOG --log-prefix "SANDBOX-DENIED: " --log-level 4`

### Security Invariants
- **No runtime bypass**: Agent code executes as UID `1000` (`sandbox`/`pvs`) and cannot modify `iptables` rules after startup.
- **Zero shell injection**: Every rule is validated by `NetworkFilter._validate_iptables_command()` (must begin with `iptables `, rejects `; | & \` $() \n`) and executed as an argument vector (`shlex.split`), never via `shell=True`.
- **Strict Pydantic config validation**: Both [`config/network_allowlist.yaml`](../../config/network_allowlist.yaml) and [`config/sandbox.yaml`](../../config/sandbox.yaml) are validated fail-fast at startup by `pvs doctor`.
- **OpenTelemetry instrumentation**: `NetworkFilter.apply_rules_in_container()` emits a `sandbox.network_filter.apply` span with `rules_count`, `latency_ms`, and `applied` attributes.

---

## Allowlist Management

Approved egress destinations are managed via GitOps in [`config/network_allowlist.yaml`](../../config/network_allowlist.yaml) (21 default entries across AI APIs, package registries, GitHub, internal observability/secrets, DNS, and loopback).

### Adding a New Endpoint
1. Edit [`config/network_allowlist.yaml`](../../config/network_allowlist.yaml) and add an entry with `destination`, `port`, `protocol`, and `description`:
   ```yaml
   - destination: "api.newservice.com"
     port: 443
     protocol: tcp
     description: "NewService API (approved via Security PR)"
   ```
2. Validate locally with `python -W error -m product_value_system doctor --repo-root .`.
3. Submit a Pull Request for security team review.
4. Apply dynamically without rebuilding container images via `sandbox.reload_allowlist()`.

### Wildcard Domains & CDN IPs
- Wildcard domains (such as `*.openai.azure.com`) cannot be resolved directly by DNS; pass concrete tenant subdomains via `NetworkFilter(..., wildcard_subdomains={"*.openai.azure.com": ["my-org.openai.azure.com"]})` or list the explicit endpoint hostname in `config/network_allowlist.yaml`.
- Dynamic CDN IPs are cached for `3600s` (1 hour) by default and can be refreshed immediately via `generate_iptables_rules(force_refresh_dns=True)` or `refresh_rules()`.

---

## Configuration & Usage

### Environment Variables (`.env`)

```bash
SANDBOX_NETWORK_FILTER_ENABLED=true
SANDBOX_NETWORK_ALLOWLIST_PATH=config/network_allowlist.yaml
SANDBOX_NETWORK_FILTER_LOG_DENIED=true
```

### Python SDK

```python
from product_value_system.sandbox import DockerSandbox, NetworkFilter, load_allowlist_from_config

# Load and inspect allowlist
rules = load_allowlist_from_config("config/network_allowlist.yaml")
nf = NetworkFilter(rules)
commands = nf.generate_iptables_rules()

# Execute code inside a filtered Docker sandbox container
sandbox = DockerSandbox(enable_network_filter=True)
result = sandbox.execute_code('print("hello from isolated sandbox")', language="python")
```

### Building Sandbox Images

```bash
docker build -t sandbox-python:latest -f deploy/docker/Dockerfile.sandbox-python deploy/docker
docker build -t sandbox-typescript:latest -f deploy/docker/Dockerfile.sandbox-typescript deploy/docker
docker run --rm sandbox-python:latest iptables --version
```

---

## Monitoring & Troubleshooting

### Auditing Blocked Connections

Denied outbound attempts emit kernel/container logs prefixed with `SANDBOX-DENIED:` (or `SANDBOX_BLOCK:` on host `DOCKER-USER` chains):

```bash
kubectl logs -n product-value-system <sandbox-pod> | grep SANDBOX-DENIED
```

### Common Issues

1. **Legitimate API call blocked**:
   - Check `config/network_allowlist.yaml` for the destination hostname/port.
   - Refresh cached DNS entries (`reload_allowlist()` or `force_refresh_dns=True`) if CDN IPs rotated.
   - For isolated debugging only, set `SANDBOX_NETWORK_FILTER_ENABLED=false`.
2. **`iptables` failed / `NET_ADMIN` missing**:
   - Confirm `iptables` is installed in the sandbox image (`docker run --rm sandbox-python:latest iptables --version`).
   - Confirm `cap_add=["NET_ADMIN"]` is permitted by the container runtime.
   - With `fallback_on_error=True` (default), non-Linux hosts or unprivileged dev environments degrade gracefully with a warning log instead of crashing.

---

See also: [Network Filtering Architecture (EVO-029)](../network-filtering.md) · [ADR-0005](../adr/ADR-0005-zero-trust-sandbox-egress-and-hybrid-secret-detection.md) · [Threat Model](../THREAT_MODEL.md)

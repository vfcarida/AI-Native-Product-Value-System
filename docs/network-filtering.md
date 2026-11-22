# Sandbox Network Egress Filtering Guide (EVO-029)

Fine-grained network egress filtering restricts outbound traffic from Docker agent sandboxes using a zero-trust allowlist enforced via dedicated Linux `iptables` chains, preventing data exfiltration while preserving access to approved LLM APIs, package registries, DNS, and localhost.

---

## Architecture Overview

```
DockerSandboxProvider.create(config)
       │
       ▼
  Container starts on bridge network (IP e.g. 172.17.0.2)
       │
       ▼
  NetworkFilter.apply_rules(container_id, container_ip)
       ├─ Resolve allowlist domains concurrently (TTL-cached IPv4)
       ├─ Create unique chain: SANDBOX_<sha256[:12]> (<= 28 chars)
       ├─ Rule 1: Allow loopback (-o lo -j ACCEPT)
       ├─ Rule 2: Allow DNS (-p udp/tcp --dport 53 -j ACCEPT)
       ├─ Rule 3: Allow established/related traffic (conntrack)
       ├─ Rule 4: Allow resolved allowlist IP/port/protocol tuples
       ├─ Rule 5: Log blocked attempts (SANDBOX_BLOCK:)
       ├─ Rule 6: Reject all other egress (icmp-port-unreachable)
       └─ Attach SANDBOX_<id> to DOCKER-USER for container IP
       │
  DockerSandboxProvider.destroy(sandbox)
       └─ Detach DOCKER-USER hook, flush & delete SANDBOX_<id> chain
```

Core modules:
- [src/product_value_system/sandbox/network_filter.py](../src/product_value_system/sandbox/network_filter.py) — `AllowlistEntry`, `NetworkFilter`, `EgressDecision`, `NetworkFilterStatus`, `load_sandbox_network_config()`
- [src/product_value_system/sandbox/iptables_manager.py](../src/product_value_system/sandbox/iptables_manager.py) — `IptablesManager`, `IptablesError`, `sanitize_chain_name()`
- [src/product_value_system/runtime/docker_sandbox.py](../src/product_value_system/runtime/docker_sandbox.py) & [src/product_value_system/sandbox/docker_sandbox.py](../src/product_value_system/sandbox/docker_sandbox.py) — `DockerSandboxProvider` lifecycle integration
- [config/sandbox.yaml](../config/sandbox.yaml) — Network allowlist configuration

---

## Configuration (`config/sandbox.yaml`)

```yaml
sandbox:
  network:
    filtering_enabled: true
    mode: allowlist  # 'allowlist' (recommended) or 'denylist'
    dns_cache_ttl_seconds: 300
    dns_resolve_retries: 2

    allowlist:
      # LLM APIs
      - domain: api.openai.com
        port: 443
        protocol: tcp
      - domain: api.anthropic.com
        port: 443
        protocol: tcp
      - domain: generativelanguage.googleapis.com
        port: 443
        protocol: tcp

      # Package registries (Build Agent)
      - domain: pypi.org
        port: 443
        protocol: tcp
      - domain: files.pythonhosted.org
        port: 443
        protocol: tcp
      - domain: registry.npmjs.org
        port: 443
        protocol: tcp

      # DNS & Localhost
      - ip: 8.8.8.8
        port: 53
        protocol: udp
      - ip: 1.1.1.1
        port: 53
        protocol: udp
      - ip: 127.0.0.1
        port: any
        protocol: any

    log_blocked_connections: true
    log_path: /var/log/sandbox_network_blocks.log
    fallback_on_error: true
```

### Adding or Removing Endpoints

- **By domain**: Add `{domain: example.org, port: 443, protocol: tcp}`. Domains are resolved to IPv4 addresses concurrently on container start and cached for `dns_cache_ttl_seconds`.
- **By IP or CIDR**: Add `{ip: 10.0.0.0/8, port: 443, protocol: tcp}` (skips DNS resolution).
- **Per-agent restriction**: Pass a minimal `SandboxConfig(allowed_hosts=["api.anthropic.com"])` for Review or Brownfield agents that do not need PyPI/npm access.

---

## Usage Examples

### 1. Load Policy with `DockerSandboxProvider`

```python
from product_value_system.sandbox import DockerSandboxProvider, SandboxConfig

provider = DockerSandboxProvider(sandbox_config_path="config/sandbox.yaml")
sandbox = await provider.create(SandboxConfig(network_enabled=True))
try:
    result = await provider.execute(sandbox, "python -c 'print(1 + 1)'")
finally:
    # Always detaches DOCKER-USER hook and removes the container's iptables chain
    await provider.destroy(sandbox)
```

### 2. Per-Sandbox Host Allowlist

```python
config = SandboxConfig(
    network_enabled=True,
    allowed_hosts=["api.openai.com:443", "api.anthropic.com:443"],
)
sandbox = await provider.create(config)
```

### 3. Diagnostic CLI Check

```bash
python -m product_value_system.sandbox test-network-filter --config config/sandbox.yaml
```

---

## Platform Requirements & Graceful Degradation

| Requirement | Purpose |
|---|---|
| **Linux host** | Native kernel `iptables` (`netfilter`) packet enforcement |
| **Root / `CAP_NET_ADMIN`** | Required to create `SANDBOX_*` chains and hook `DOCKER-USER` |
| **Docker Engine** | Bridge networking with per-container IP attribution |
| **Optional `pyroute2>=0.7.0`** | Linux-only extra (`pip install -e ".[sandbox]"`) |

On **Windows**, **macOS**, or unprivileged CI runners where `iptables` is unavailable:
- With `fallback_on_error: true` (default), `NetworkFilter.apply_rules()` logs a warning and returns `NetworkFilterStatus(applied=False, degraded_fallback=True)`, degrading cleanly to Docker's standard `network_enabled` (`bridge` / `none`) setting.
- With `fallback_on_error: false` (strict production mode), `DockerSandboxProvider.create()` destroys the container immediately and raises `SandboxError` so no unfiltered container can run.

---

## Security Best Practices & Limitations

1. **Zero-Trust Default**: Keep `mode: allowlist` and remove package registries (`pypi.org`, `registry.npmjs.org`) for read-only Review or Discovery sandboxes.
2. **Audit Blocked Connections**: Monitor `SANDBOX_BLOCK:` entries in kernel logs (`dmesg` / `journalctl -k`) and `log_path` to detect exfiltration attempts or misconfigured endpoints.
3. **Dynamic IP Refresh**: Call `await network_filter.refresh_rules(container_id, container_ip=ip)` periodically for long-lived sandboxes whose allowlisted SaaS APIs rotate IPs, or specify provider CIDR ranges where published.
4. **Known Limitations**:
   - Allowing DNS port 53 carries residual DNS-tunneling risk; restrict egress DNS to approved internal resolvers when deployed inside a corporate VPC.
   - `iptables` filters at L3/L4 (IP/port/protocol) and does not perform TLS payload inspection.

---

## Troubleshooting & Rollback

- **Instant Rollback**: Set `sandbox.network.filtering_enabled: false` in [config/sandbox.yaml](../config/sandbox.yaml), or pass `SandboxConfig(network_filtering_enabled=False)`.
- **`iptables not available`**: Expected on macOS/Windows development machines; confirm `fallback_on_error: true` or run inside a Linux VM/CI runner with `sudo` / `NET_ADMIN`.
- **Domain fails to resolve**: Check host DNS connectivity; `NetworkFilter` retries resolution with exponential backoff (`dns_resolve_retries`) and retains stale cached IPs during transient DNS glitches.
- **Orphaned iptables chains after crash**: Call `network_filter.iptables.cleanup_all()` or inspect chains with `sudo iptables -L -n | grep SANDBOX_`.

---

## Related Documentation

- [Threat Model](THREAT_MODEL.md)
- [Security Testing Guide](SECURITY_TESTING.md)
- [Secret Management](SECRET_MANAGEMENT.md)
- [Architecture & Diagrams](ARCHITECTURE.md)

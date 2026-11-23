# Product Value System — Kubernetes Helm Chart

Production-hardened Helm v3 chart for deploying the **AI-Native Product Value System** control plane (`/health/live`, `/health/ready`, `/slos`, `/metrics`, `/devx`).

## Security & Operability Defaults

- **Restricted Pod Security Standard (PSS)**: Non-root UID/GID `10001` (matching the multi-stage `Dockerfile`), `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, `capabilities.drop: ["ALL"]`, `seccompProfile.type: RuntimeDefault`, and `automountServiceAccountToken: false`.
- **Zero-Trust NetworkPolicy**: Default-deny egress permitting DNS (53), HTTPS (443) for LLM APIs (optional CIDR allowlist), and configured Redis/OTLP ports only.
- **High Availability**: Rolling updates (`maxUnavailable: 0`), `PodDisruptionBudget`, `HorizontalPodAutoscaler` (`autoscaling/v2`), and `topologySpreadConstraints`.
- **Observability & Privacy**: Optional Prometheus Operator `ServiceMonitor` scraping `/metrics`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT="false"`, and `PVS_DEVX_AGGREGATE_ONLY="true"` by default.

## Quick Start

```bash
# 1. Create a Kubernetes Secret from your local .env file (avoids leaking keys in shell history)
kubectl create secret generic pvs-provider-secrets \
  --namespace pvs \
  --from-env-file=.env

# 2. Lint and render manifests locally
helm lint deploy/helm/product-value-system
helm template pvs deploy/helm/product-value-system \
  --set secrets.existingSecret=pvs-provider-secrets

# 3. Install or upgrade
helm upgrade --install pvs deploy/helm/product-value-system \
  --namespace pvs --create-namespace \
  --set secrets.existingSecret=pvs-provider-secrets \
  --set config.redisHost=redis-master.pvs.svc.cluster.local \
  --set serviceMonitor.enabled=true
```

## Key Configuration Values

| Value | Default | Description |
|---|---|---|
| `replicaCount` | `2` | Static replicas (used when `autoscaling.enabled=false`) |
| `image.repository` | `ghcr.io/vfcarida/product-value-system` | Container image repository |
| `secrets.existingSecret` | `""` | Existing Secret name for LLM & metrics API keys (**recommended**) |
| `config.cacheBackend` | `redis` | Recommended multi-node prompt cache backend |
| `config.otelCaptureMessageContent` | `"false"` | Suppress raw prompts/completions in OTel spans by default |
| `networkPolicy.enabled` | `true` | Enforce default-deny ingress/egress `NetworkPolicy` |
| `serviceMonitor.enabled` | `false` | Enable Prometheus Operator `ServiceMonitor` for `/metrics` |
| `slo.enabled` | `false` | Deploy Prometheus Operator `PrometheusRule` CRD (12 SLO/saturation alerts + 4 recording rules) |
| `otelCollector.enabled` | `false` | Deploy companion hardened OpenTelemetry Collector (OTLP 4317/4318 -> Prometheus 8889) |
| `grafanaDashboards.enabled` | `false` | Render Grafana sidecar-discoverable dashboard ConfigMap (`grafana_dashboard: "1"`) |
| `vault.enabled` | `false` | Enable HashiCorp Vault Agent sidecar injection & Kubernetes auth (`VAULT_ADDR`) |
| `autoscaling.enabled` | `true` | Enable `HorizontalPodAutoscaler` (`2`–`10` replicas) |

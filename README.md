# AI-Native Product Value System

**Version**: 0.1.0  
**Status**: 🚧 Reference Implementation (Educational Use Only)

---

## Purpose

This is a **reference implementation** demonstrating AI-native product development from problem → outcome → discovery → specification → build → review → release → learn.

It shows how to build **quality, security, and governance** into AI-accelerated product development.

**⚠️ This is NOT production software.** It is for research, education, and experimentation.

---

## Key Features

- ✅ **Value Stream Orchestration**: Complete problem-to-outcome workflow
- ✅ **Modular Agent System**: Discovery, Spec, Build, Review agents
- ✅ **Policy-Based Governance**: Cedar authorization for agent actions
- ✅ **Sandbox Isolation**: Docker-based secure execution
- ✅ **Quality Gates**: Deterministic + AI-based evaluations
- ✅ **Observability**: OpenTelemetry tracing with cost tracking
- ✅ **Security**: Secret detection, input validation, threat model

---

## Prerequisites

- **Python 3.12+**
- **Docker** (Desktop or Engine)
- **Git**
- **Model API Key** (OpenAI or Anthropic; optional when using the built-in Mock adapter)

---

## Model Providers

**Implemented**:
- ✅ **OpenAI** (`OpenAIModelAdapter` — GPT-4, GPT-4 Turbo, GPT-3.5 Turbo)
- ✅ **Anthropic** (`AnthropicAdapter` — Claude 3.5 Sonnet, Claude 3 Opus, Claude 3 Haiku)
- ✅ **Mock** (`MockModelAdapter` — deterministic local testing)

**Planned**:
- 🔜 Google (Gemini)
- 🔜 Azure OpenAI
- 🔜 Local models (Ollama)

### Configuration

See [config/model_adapters.yaml](config/model_adapters.yaml):

```yaml
default_adapter: anthropic

adapters:
  openai:
    api_key: ${OPENAI_API_KEY}
    model: gpt-4

  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-3-5-sonnet-20241022
```

### Switching to Anthropic

1. Set your API key:
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   ```
2. Select the Anthropic adapter (`AnthropicAdapter`) and model (`claude-3-5-sonnet-20241022`, `claude-3-opus-20240229`, or `claude-3-haiku-20240307`) in `config/model_adapters.yaml` or agent configuration.

**Cost Comparison (per 1M tokens)**:
- OpenAI GPT-4 Turbo: \$10 input / \$30 output
- Anthropic Claude 3.5 Sonnet: \$3 input / \$15 output
- Anthropic Claude 3 Haiku: \$0.25 input / \$1.25 output

---

## Quick Start

```bash
# 1. Clone repository
git clone https://github.com/<org>/ai-native-product-value-system.git
cd ai-native-product-value-system

# 2. Install dependencies
make install

# 3. Install Git hooks (secret scanning)
./scripts/install-git-hooks.sh

# 4. Set API key
export OPENAI_API_KEY="sk-..."

# 5. Run example
make demo
```

**Expected Output**: Value stream runs from problem → outcome → spec → build → review → release, producing artifacts in `artifacts/runs/<run-id>/`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                  AI-Native Product Value System              │
│                                                              │
│  Problem → Discovery → Specification → Build → Review       │
│           → Release → Run → Learn → [feedback]              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
        ↕                    ↕                    ↕
   [User Input]      [Model APIs]         [Git Repository]
   [problem.yaml]    [OpenAI/Anthropic]   [Artifacts]
```

**Key Components**:

- **Orchestrator**: State machine coordinating agents
- **Agents**: Specialized AI agents (Discovery, Spec, Build, Review)
- **Harness**: Context, tools, memory, guardrails for agents
- **Policy Engine**: Cedar authorization (least-privilege)
- **Sandbox**: Docker containers (non-root, isolated)
- **Evaluation**: Quality gates (schema, tests, AI judges)

See [Architecture Documentation](docs/ARCHITECTURE.md) for details.

---

## Examples

### Greenfield: Payment Processing System

```bash
pvs run examples/greenfield/problem_payment_flow.yaml
```

**Output**: Complete implementation (code, tests, docs) for payment flow.

### Brownfield: Legacy API Documentation

```bash
pvs run examples/brownfield/problem_document_legacy.yaml
```

**Output**: Generated documentation for existing legacy API code.

---

## Documentation

- **[Architecture](docs/ARCHITECTURE.md)** — System design and diagrams
- **[API Reference](docs/reference/README.md)** — Complete API documentation
- **[How-To Guides](docs/howto/README.md)** — Task-oriented guides
- **[Threat Model](docs/THREAT_MODEL.md)** — Security analysis (OWASP LLM Top 10)
- **[Secret Management](docs/SECRET_MANAGEMENT.md)** — Best practices

---

## Development

```bash
# Run unit tests
make test-unit

# Run all tests
make test

# Lint code
make lint

# Build Docker image
make docker-build

# Run in Docker
make docker-run
```

---

## Security

**Important**: This is a reference implementation, NOT production software.

- ✅ Cedar policy engine for authorization
- ✅ Docker sandbox (non-root user, capabilities dropped)
- ✅ Secret detection (pre-commit hooks + CI)
- ✅ Input validation and output sanitization
- ⚠️ No formal security audit
- ⚠️ Docker isolation (not microVMs)
- ⚠️ Regex-based secret detection (not ML)

See [THREAT_MODEL.md](docs/THREAT_MODEL.md) for comprehensive security analysis.

**Report vulnerabilities**: See [SECURITY.md](docs/SECURITY.md)

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

**Areas for Contribution**:

- Additional agents (e.g., deployment agent, monitoring agent)
- New model providers (e.g., local models via Ollama)
- Enhanced evaluations (trajectory judges, multi-turn)
- Cloud deployment (Kubernetes, AWS ECS)
- Advanced observability (dashboards, alerts)

---

## License

Apache License 2.0

See [LICENSE](LICENSE) for details.

---

## Citation

If you use this in research or education, please cite:

```bibtex
@software{ai_native_pvs_2026,
  title = {AI-Native Product Value System},
  author = {AI-Native Product Value System Contributors},
  year = {2026},
  version = {0.1.0},
  url = {https://github.com/<org>/ai-native-product-value-system}
}
```

---

## Acknowledgments

- **Claude (Anthropic)** — Implementation assistance
- **OWASP LLM Top 10** — Security threat modeling
- **Cedar (AWS)** — Policy language
- **C4 Model** — Architecture diagrams

---

## Roadmap

- **v0.2.0**: Multi-agent collaboration, MCP support
- **v0.3.0**: Cloud deployment, model gateway
- **v1.0.0**: Production hardening, security audit

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for detailed roadmap.

---

## Support

- **Documentation**: [docs/](docs/)
- **Issues**: [GitHub Issues](https://github.com/<org>/<repo>/issues)
- **Discussions**: [GitHub Discussions](https://github.com/<org>/<repo>/discussions)

---

**Status**: v0.1.0 Reference Implementation (Educational Use Only)  
**Last Updated**: 2026-11-18

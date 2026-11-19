# Configuration Reference: Environment Variables

The AI-native Product Value System follows 12-Factor configuration conventions.

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for `OpenAIModelAdapter` | *(none)* |
| `ANTHROPIC_API_KEY` | Anthropic API key for `AnthropicAdapter` | *(none)* |
| `ANTHROPIC_BASE_URL` | Optional Anthropic API base URL override | `https://api.anthropic.com` |
| `ANTHROPIC_DEFAULT_MODEL` | Optional default Claude model identifier | `claude-3-5-sonnet-20241022` |
| `PVS_DB_PATH` | Path to SQLite persistence database | `pvs.db` |
| `PVS_ARTIFACT_ROOT` | Root directory for value stream run artifacts and checkpoints | `artifacts` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry collector gRPC endpoint | `http://localhost:4317` |

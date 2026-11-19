# Configuration Reference: Environment Variables

The AI-native Product Value System follows 12-Factor configuration conventions.

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for `OpenAIModelAdapter` | *(none)* |
| `PVS_DB_PATH` | Path to SQLite persistence database | `pvs.db` |
| `PVS_ARTIFACT_ROOT` | Root directory for value stream run artifacts and checkpoints | `artifacts` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OpenTelemetry collector gRPC endpoint | `http://localhost:4317` |

"""
External adapter interfaces and implementations (models, sandboxes, knowledge).
"""

from typing import Any

from product_value_system.adapters.anthropic_adapter import (
    AnthropicAdapter,
    AnthropicModelAdapter,
)
from product_value_system.adapters.azure_openai_adapter import (
    AzureOpenAIAdapter,
    AzureOpenAIModelAdapter,
)
from product_value_system.adapters.embedding_adapter import (
    EmbeddingAdapter,
    GeminiEmbeddingAdapter,
    LocalHashEmbeddingAdapter,
    OpenAIEmbeddingAdapter,
    SentenceTransformerEmbeddingAdapter,
    create_embedding_adapter,
)
from product_value_system.adapters.gemini_adapter import (
    GeminiAdapter,
    GeminiModelAdapter,
)
from product_value_system.adapters.mock_adapter import MockModelAdapter
from product_value_system.adapters.model_adapter import (
    Message,
    ModelAdapter,
    ModelChunk,
    ModelError,
    ModelResponse,
    ModelUsage,
    NetworkError,
    RateLimitError,
    Role,
    StructuredOutputError,
    Tool,
    ToolCall,
    Usage,
    extract_json_payload,
    parse_structured_output,
    schema_to_json_schema,
)
from product_value_system.adapters.ollama_adapter import (
    OllamaAdapter,
    OllamaModelAdapter,
)
from product_value_system.adapters.openai_adapter import (
    OpenAIAdapter,
    OpenAIModelAdapter,
)


def create_adapter(provider: str, model: str | None = None, **kwargs: Any) -> ModelAdapter:
    """Instantiate a concrete ``ModelAdapter`` by provider identifier.

    Supported providers: ``openai``, ``anthropic``, ``gemini`` (or ``google``),
    ``azure_openai`` (or ``azure``), ``ollama`` (or ``local``), and ``mock``.
    """
    normalized = provider.strip().lower().replace("-", "_")
    if normalized == "openai":
        return OpenAIModelAdapter(model=model or "gpt-4o", **kwargs)
    if normalized == "anthropic":
        return AnthropicModelAdapter(model=model or "claude-3-5-sonnet-20241022", **kwargs)
    if normalized in {"gemini", "google", "google_gemini", "vertex_ai"}:
        return GeminiModelAdapter(model=model or "gemini-2.5-flash", **kwargs)
    if normalized in {"azure", "azure_openai"}:
        deployment = kwargs.pop("deployment", None) or model
        return AzureOpenAIModelAdapter(deployment=deployment, model=model, **kwargs)
    if normalized in {"ollama", "local"}:
        return OllamaModelAdapter(model=model or "llama3.2", **kwargs)
    if normalized == "mock":
        return MockModelAdapter(**kwargs)
    raise ValueError(
        f"Unsupported model provider: {provider!r}. Expected one of: "
        "openai, anthropic, gemini, azure_openai, ollama, mock."
    )


__all__ = [
    "AnthropicAdapter",
    "AnthropicModelAdapter",
    "AzureOpenAIAdapter",
    "AzureOpenAIModelAdapter",
    "EmbeddingAdapter",
    "GeminiAdapter",
    "GeminiEmbeddingAdapter",
    "GeminiModelAdapter",
    "LocalHashEmbeddingAdapter",
    "Message",
    "MockModelAdapter",
    "ModelAdapter",
    "ModelChunk",
    "ModelError",
    "ModelResponse",
    "ModelUsage",
    "NetworkError",
    "OllamaAdapter",
    "OllamaModelAdapter",
    "OpenAIAdapter",
    "OpenAIEmbeddingAdapter",
    "OpenAIModelAdapter",
    "RateLimitError",
    "Role",
    "SentenceTransformerEmbeddingAdapter",
    "StructuredOutputError",
    "Tool",
    "ToolCall",
    "Usage",
    "create_adapter",
    "create_embedding_adapter",
    "extract_json_payload",
    "parse_structured_output",
    "schema_to_json_schema",
]

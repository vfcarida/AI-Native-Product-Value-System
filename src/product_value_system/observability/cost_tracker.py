"""
Cost tracking for model invocations.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# Pricing (USD per 1M tokens)
MODEL_PRICING: dict[str, dict[str, Decimal]] = {
    # OpenAI
    "gpt-4": {"input": Decimal("30.00"), "output": Decimal("60.00")},
    "gpt-4-turbo": {"input": Decimal("10.00"), "output": Decimal("30.00")},
    "gpt-3.5-turbo": {"input": Decimal("0.50"), "output": Decimal("1.50")},
    # Anthropic
    "claude-3-5-sonnet-20241022": {"input": Decimal("3.00"), "output": Decimal("15.00")},
    "claude-3-5-sonnet-latest": {"input": Decimal("3.00"), "output": Decimal("15.00")},
    "claude-3-opus-20240229": {"input": Decimal("15.00"), "output": Decimal("75.00")},
    "claude-3-sonnet-20240229": {"input": Decimal("3.00"), "output": Decimal("15.00")},
    "claude-3-haiku-20240307": {"input": Decimal("0.25"), "output": Decimal("1.25")},
    # Google
    "gemini-1.5-pro": {"input": Decimal("7.00"), "output": Decimal("21.00")},
    "gemini-1.5-flash": {"input": Decimal("0.35"), "output": Decimal("1.05")},
}


@dataclass
class CostBreakdown:
    """Cost breakdown for a model invocation."""

    model: str
    input_tokens: int
    output_tokens: int
    input_cost_usd: Decimal
    output_cost_usd: Decimal
    total_cost_usd: Decimal

    def to_dict(self) -> dict[str, Any]:
        """Serialize cost breakdown to dictionary."""
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost_usd": float(self.input_cost_usd),
            "output_cost_usd": float(self.output_cost_usd),
            "total_cost_usd": float(self.total_cost_usd),
        }


class CostTracker:
    """
    Tracks costs across model invocations.
    """

    def __init__(self, custom_pricing: dict[str, dict[str, Decimal]] | None = None) -> None:
        """
        Initialize cost tracker.

        Args:
            custom_pricing: Override default pricing (for self-hosted models)
        """
        self.pricing: dict[str, dict[str, Decimal]] = MODEL_PRICING.copy()
        if custom_pricing:
            self.pricing.update(custom_pricing)

        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = Decimal("0.0")
        self.calls_by_model: dict[str, int] = {}

    def compute_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> CostBreakdown:
        """
        Compute cost for a model invocation.

        Args:
            model: Model identifier
            input_tokens: Input token count
            output_tokens: Output token count

        Returns:
            CostBreakdown with computed costs

        Raises:
            ValueError: If model not in pricing table
        """
        if model not in self.pricing:
            raise ValueError(f"Model '{model}' not in pricing table")

        pricing = self.pricing[model]

        input_millions = Decimal(input_tokens) / Decimal("1000000")
        output_millions = Decimal(output_tokens) / Decimal("1000000")

        input_cost = pricing["input"] * input_millions
        output_cost = pricing["output"] * output_millions
        total_cost = input_cost + output_cost

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost_usd += total_cost
        self.calls_by_model[model] = self.calls_by_model.get(model, 0) + 1

        return CostBreakdown(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            total_cost_usd=total_cost,
        )

    def get_summary(self) -> dict[str, Any]:
        """Get cost summary."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": float(self.total_cost_usd),
            "calls_by_model": self.calls_by_model,
        }

    def reset(self) -> None:
        """Reset all counters."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = Decimal("0.0")
        self.calls_by_model.clear()

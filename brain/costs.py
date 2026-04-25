"""Model cost estimation and tracking.

Per-model pricing (USD per 1M tokens). Updated as of 2025-04.
Claude CLI returns total_cost_usd for claude models, but we still
need estimates for ollama (free) and for pre-response budgeting.

Usage:
    from brain.costs import estimate_cost, MODEL_COSTS

    cost = estimate_cost("claude-sonnet-4-6", input_tokens=2000, output_tokens=500)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class ModelCost:
    """Cost per 1M tokens (USD)."""
    input: float
    output: float
    name: str  # human-readable


# Pricing table — add new models here, everything else adapts.
MODEL_COSTS: dict[str, ModelCost] = {
    # Claude family
    "claude-opus-4-7":          ModelCost(15.0, 75.0, "Opus 4"),
    "claude-sonnet-4-6":        ModelCost(3.0, 15.0, "Sonnet 4"),
    "claude-haiku-4-5-20251001": ModelCost(0.80, 4.0, "Haiku 4.5"),

    # Gemini (free tier)
    "gemini-2.5-flash":        ModelCost(0.0, 0.0, "Gemini 2.5 Flash (free)"),
    "gemini":                   ModelCost(0.0, 0.0, "Gemini (free)"),

    # Ollama (local, free)
    "qwen2.5:3b":              ModelCost(0.0, 0.0, "Qwen 2.5 3B (local)"),
    "ollama":                   ModelCost(0.0, 0.0, "Ollama (local)"),

    # Codex (ChatGPT Pro flat fee — effectively free per-call)
    "codex":                    ModelCost(0.0, 0.0, "Codex (Pro subscription)"),
    "opencode":                 ModelCost(0.0, 0.0, "OpenCode"),
}

# Default sonnet cost when model is None (CLI default)
DEFAULT_MODEL = "claude-sonnet-4-6"


def estimate_cost(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    """Estimate cost in USD for a request.

    Returns 0.0 for free/local models. For Claude models, this is an
    estimate — the CLI's total_cost_usd (when available) is more accurate
    since it accounts for caching discounts.
    """
    model_key = model or DEFAULT_MODEL
    costs = MODEL_COSTS.get(model_key)
    if not costs:
        # Unknown model — assume sonnet pricing as safe default
        costs = MODEL_COSTS[DEFAULT_MODEL]

    return (
        (input_tokens * costs.input / 1_000_000)
        + (output_tokens * costs.output / 1_000_000)
    )


def estimate_cost_from_chars(
    model: str | None,
    input_chars: int = 0,
    output_chars: int = 0,
) -> float:
    """Rough cost estimate from character counts (~4 chars per token)."""
    return estimate_cost(
        model,
        input_tokens=input_chars // 4,
        output_tokens=output_chars // 4,
    )


@dataclass
class CostReport:
    """Returned alongside responses for cost awareness."""
    model: str
    model_name: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    actual_cost_usd: float | None  # from CLI when available


def build_cost_report(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    actual_cost_usd: float | None = None,
) -> CostReport:
    """Build a cost report for a completed request."""
    model_key = model or DEFAULT_MODEL
    costs = MODEL_COSTS.get(model_key)
    model_name = costs.name if costs else model_key

    return CostReport(
        model=model_key,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        estimated_cost_usd=estimate_cost(model, input_tokens, output_tokens),
        actual_cost_usd=actual_cost_usd,
    )

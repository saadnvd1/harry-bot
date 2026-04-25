"""Rate-limit detection, automatic adapter fallback, and quality escalation.

Two separate retry mechanisms:
  1. Rate-limit fallback — switch to a different provider (codex, ollama)
  2. Quality escalation — retry with a stronger model when response is
     too shallow for the task's complexity level

Usage:
    from agents.fallback import is_rate_limited, get_fallback, should_escalate, get_escalation

    if is_rate_limited(error_message):
        next_agent = get_fallback(current_agent)

    if should_escalate(response_text, complexity="complex"):
        agent, model = get_escalation(current_model)
"""

from __future__ import annotations

import re
from typing import Literal

# --- Rate limit / overload detection ---
# Rate limit patterns adapted for CLI stderr/error strings.

_RATE_LIMIT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"rate[_ ]limit",
        r"too many requests",
        r"\b429\b",
        r"throttl",
        r"exceeded.*quota",
        r"quota exceeded",
        r"overloaded",
        r"overloaded_error",
        r"high demand",
        r"capacity",
        r"usage limit",
        r"tokens per minute",
        r"requests per minute",
        r"try again later",
        r"resource[_ ]exhausted",
    ]
]


def is_rate_limited(error_msg: str) -> bool:
    """Check if an error message indicates rate limiting or overload."""
    if not error_msg:
        return False
    return any(p.search(error_msg) for p in _RATE_LIMIT_PATTERNS)


# --- Context overflow detection ---

_CONTEXT_OVERFLOW_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"prompt is too long",
        r"context.*(too long|overflow|exceeded|limit)",
        r"maximum context length",
        r"token limit",
        r"input too long",
    ]
]


def is_context_overflow(error_msg: str) -> bool:
    """Check if the error is a context window overflow (session too large)."""
    if not error_msg:
        return False
    return any(p.search(error_msg) for p in _CONTEXT_OVERFLOW_PATTERNS)


# --- Transient Claude CLI bugs (retry same adapter with fresh session) ---
# Upstream bug in claude-code CLI v2.1.19+ where parallel tool calls in
# --print mode occasionally emit duplicate tool_use ids, causing a 400 on
# the next turn. Intermittent — same prompt usually succeeds on retry.
# Refs: anthropics/claude-code#20508, #18131, #20693, #20751, #6836.

_TRANSIENT_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"tool_use.*must be unique",
        r"tool use concurrency",
    ]
]


def is_transient(error_msg: str) -> bool:
    """Check for known transient CLI bugs that recover on retry."""
    if not error_msg:
        return False
    return any(p.search(error_msg) for p in _TRANSIENT_PATTERNS)


# --- Fallback chain (rate limits → different provider) ---
# codex uses ChatGPT Pro (flat fee), ollama is free local.

FALLBACK_CHAIN: dict[str, list[str]] = {
    "claude":  ["codex", "ollama"],
    "codex":   ["claude", "ollama"],
    "gemini":  ["claude"],  # gemini rate-limited → haiku via claude adapter
}


def get_fallback(current_agent: str, already_tried: set[str] | None = None) -> str | None:
    """Return the next fallback agent, or None if chain exhausted."""
    tried = already_tried or set()
    chain = FALLBACK_CHAIN.get(current_agent, [])
    for agent in chain:
        if agent not in tried:
            return agent
    return None


# --- Quality escalation (weak response → stronger model) ---
# Only escalates within the same provider. Goes up one tier max to
# avoid runaway costs. Single retry — if opus can't handle it, nothing will.

ESCALATION_CHAIN: dict[str | None, tuple[str, str]] = {
    # current model → (agent, stronger model)
    # None = CLI default (sonnet)
    None:                          ("claude", "claude-opus-4-7"),
    "claude-sonnet-4-6":           ("claude", "claude-opus-4-7"),
    "claude-haiku-4-5-20251001":   ("claude", None),  # haiku → sonnet (default)
}

# Minimum response length by complexity before escalation triggers.
# Complex tasks that return < 50 chars are almost certainly truncated/failed.
_MIN_RESPONSE_CHARS: dict[str, int] = {
    "simple": 0,     # never escalate simple tasks
    "medium": 20,    # very short responses might indicate failure
    "complex": 50,   # complex tasks should produce substantial output
}


def should_escalate(
    response_text: str,
    complexity: Literal["simple", "medium", "complex"],
    current_model: str | None = None,
) -> bool:
    """Check if a response is too weak for the task's complexity.

    Only triggers for medium/complex tasks with suspiciously short responses.
    Won't escalate past opus (no entry in ESCALATION_CHAIN).
    """
    # Never escalate simple tasks
    if complexity == "simple":
        return False

    # Can't escalate if already at top or no escalation path
    if current_model not in ESCALATION_CHAIN:
        return False

    # Check response length against minimum for this complexity
    min_chars = _MIN_RESPONSE_CHARS.get(complexity, 0)
    response = (response_text or "").strip()
    if len(response) < min_chars:
        return True

    # Empty or near-empty response for any non-simple task
    if len(response) < 10 and complexity != "simple":
        return True

    return False


def get_escalation(current_model: str | None) -> tuple[str, str | None] | None:
    """Return (agent, stronger_model) or None if can't escalate."""
    entry = ESCALATION_CHAIN.get(current_model)
    if entry:
        return entry
    return None

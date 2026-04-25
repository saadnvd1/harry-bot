"""Tiered enrichment — controls how much context each message gets.

Maps complexity (from brain/router.py) to enrichment budgets instead of
running its own independent classifier. Single source of truth for
message classification.

Tiers:
  none    — routed to ollama/haiku, no enrichment needed (0 extra tokens)
  light   — system prompt + date only (~0 extra tokens)
  normal  — + relevant memories + short history (~2-3k tokens)
  full    — + vault context + all memories + full history (~7k tokens)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class EnrichTier:
    name: str
    vault: bool         # load vault context
    vault_max_files: int
    vault_max_chars: int
    memories: int       # number of memory files to load
    memory_max_chars: int  # per-memory char cap
    history_turns: int  # conversation history turns to load


# Tier definitions
TIERS = {
    "none": EnrichTier("none", vault=False, vault_max_files=0, vault_max_chars=0,
                        memories=0, memory_max_chars=0, history_turns=0),
    "light": EnrichTier("light", vault=False, vault_max_files=0, vault_max_chars=0,
                         memories=0, memory_max_chars=0, history_turns=0),
    "normal": EnrichTier("normal", vault=False, vault_max_files=3, vault_max_chars=6000,
                          memories=3, memory_max_chars=300, history_turns=6),
    "full": EnrichTier("full", vault=True, vault_max_files=5, vault_max_chars=12000,
                        memories=5, memory_max_chars=500, history_turns=10),
}

# Complexity → enrichment tier mapping.
# simple tasks don't need context, complex tasks get everything.
_COMPLEXITY_TO_TIER: dict[str, str] = {
    "simple":  "light",
    "medium":  "normal",
    "complex": "full",
}


def classify(message: str, agent: str, model: str | None,
             complexity: str | None = None) -> EnrichTier:
    """Map to enrichment tier. Uses complexity from router when available.

    Falls back to agent-based heuristic if complexity not provided
    (for proactive jobs that bypass the router).
    """
    # Non-claude agents handle their own context
    if agent not in ("claude",):  # ollama, gemini, codex etc
        return TIERS["none"]

    # Use complexity from router (preferred — single source of truth)
    if complexity:
        tier_name = _COMPLEXITY_TO_TIER.get(complexity, "normal")
        return TIERS[tier_name]

    # Fallback for proactive jobs (briefing/checkin/reflection) that
    # don't go through the router — default to normal
    return TIERS["normal"]

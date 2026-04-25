"""Agent + model routing for chat messages.

Decision order:
  1. Explicit prefix (`!ollama`, `!codex`, `!opencode`, `!h`, `!s`, `!ollama:model`)
  2. Script shortcut → handled upstream in brain/claude.py (not here)
  3. Usage check: under 70% → opus for everything (except acks → gemini)
  4. Over 70% → tiered complexity routing (simple/medium/complex)

Returns Route with agent, model, complexity, and stripped message.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Literal

from brain.claude import HAIKU_PATTERNS

logger = logging.getLogger(__name__)


Complexity = Literal["simple", "medium", "complex"]

# --- Usage-aware routing ---
USAGE_THRESHOLD = 0.70  # switch to tiered routing above this
USAGE_CACHE_TTL = 300   # 5 min between API checks
DEFAULT_MODEL_FULL = "claude-sonnet-4-6"

_usage_cache: dict[str, float] = {"utilization": 0.0, "checked_at": 0.0}


def _get_usage_utilization() -> float:
    """Get max utilization (5h or 7d), cached for 5 min. Returns 0.0 on error."""
    now = time.time()
    if now - _usage_cache["checked_at"] < USAGE_CACHE_TTL:
        return _usage_cache["utilization"]

    try:
        from tools.usage import get_token, fetch_usage
        token = get_token()
        if not token:
            return _usage_cache["utilization"]
        usage = fetch_usage(token)
        if not usage:
            return _usage_cache["utilization"]

        five_pct = usage.get("five_hour", {}).get("utilization", 0)
        seven_pct = usage.get("seven_day", {}).get("utilization", 0)
        util = max(five_pct, seven_pct) / 100.0  # API returns 0-100, normalize to 0-1
        _usage_cache["utilization"] = util
        _usage_cache["checked_at"] = now
        logger.info("usage check: %.0f%% (5h=%.0f%%, 7d=%.0f%%)",
                     util * 100, five_pct, seven_pct)
        return util
    except Exception as e:
        logger.warning("usage check failed: %s", e)
        return _usage_cache["utilization"]


def is_conserving() -> bool:
    """True when usage is high enough to switch to tiered routing."""
    return _get_usage_utilization() >= USAGE_THRESHOLD


@dataclass
class Route:
    agent: str
    model: str | None
    message: str
    reason: str
    complexity: Complexity = "medium"


# Haiku for short acks — cheaper than sonnet, better than ollama for conversation
HAIKU_MODEL = "claude-haiku-4-5-20251001"

# --- Model tier mapping ---
# Configurable: change these to swap models without touching routing logic.
MODEL_TIERS: dict[Complexity, tuple[str, str | None]] = {
    "simple":  ("ollama", None),                      # free local (keep_alive=24h avoids cold starts)
    "medium":  ("claude", None),                      # sonnet (CLI default)
    "complex": ("claude", "claude-opus-4-7"),          # opus for heavy tasks
}


# --- Explicit prefix shortcuts ---
PREFIX_AGENT_SHORTCUTS = {
    "!h ": ("claude", HAIKU_MODEL),
    "!s ": ("claude", "claude-sonnet-4-6"),
    "!opus ": ("claude", "claude-opus-4-7"),
    "!claude ": ("claude", None),
    "!codex ": ("codex", None),
    "!opencode ": ("opencode", None),
    "!ollama ": ("ollama", None),
    "!local ": ("ollama", None),
    "!free ": ("ollama", None),
    "!gemini ": ("gemini", None),
}

# `!agent:model ...` generic form
GENERIC_PREFIX = re.compile(r"^!(\w+)(?::([\w./-]+))?\s+(.*)", flags=re.DOTALL)


# --- Simple: trivial factual lookups, math, acks ---
TRIVIAL_PATTERNS = [
    # Math & units
    r"\bwhat(?:'s| is)?\s+\d+\s*[\+\-\*/xX]\s*\d+",
    r"\bconvert\s+\d",
    r"\bhow many\s+\w+\s+(?:in|are in|equals)",
    # Time & date trivia
    r"^\s*what(?:'s| is) (?:the )?(time|date)\b",
    r"^\s*what (time|date)",
    r"^\s*(current|utc|gmt) (time|date)",
    # Definitions & spellings
    r"^\s*define\s+\w+",
    r"^\s*spell\s+\w+",
    # Factual geographic/encyclopedic
    r"^\s*capital of\b",
    r"^\s*who (is|was)\s+\w+\s*\??$",
    # Simple quick-lookup asks
    r"^\s*translate\s+",
    r"^\s*summarize in one line\b",
]
_TRIVIAL_RE = [re.compile(p, flags=re.IGNORECASE) for p in TRIVIAL_PATTERNS]

# Short acks that need no reasoning
_ACK_PATTERNS = {
    "yes", "no", "yeah", "yep", "nope", "ok", "okay", "sure", "thanks",
    "thank you", "thx", "ty", "cool", "nice", "got it", "k", "kk",
    "lol", "haha", "lmao", "true", "yea", "nah", "bet",
}


# --- Complex: signals that need heavy reasoning ---
_COMPLEX_SIGNALS = [
    # Architecture & planning
    "architect", "design", "plan", "trade-off", "tradeoff", "compare and contrast",
    "pros and cons", "evaluate", "strategy",
    # Multi-step reasoning
    "step by step", "walk me through", "break down", "analyze",
    "think through", "figure out", "help me decide",
    # Code architecture
    "refactor", "redesign", "migrate", "rewrite", "overhaul",
    "system design", "data model", "schema design",
    # Deep analysis
    "debug this", "why does", "root cause", "investigate",
    "security review", "code review", "audit",
    # Long-form generation
    "write a detailed", "comprehensive", "in-depth", "thorough",
    "explain in detail", "full analysis",
]

_COMPLEX_RE = [
    # Multi-part questions (3+ question marks)
    re.compile(r"(\?.*){3,}"),
    # Numbered lists in the prompt (user outlining multi-step ask)
    re.compile(r"^\s*[1-9]\.", re.MULTILINE),
    # Code blocks (user pasting code for review)
    re.compile(r"```[\s\S]{200,}```"),
]


def classify_complexity(message: str) -> Complexity:
    """Deterministic complexity classification. No LLM calls.

    Heuristics in order of specificity:
    1. Short acks / trivial patterns → simple
    2. Complex signals / long multi-part messages → complex
    3. Everything else → medium
    """
    msg = message.strip()
    msg_lower = msg.lower()

    # --- Simple ---
    # Short acks
    if msg_lower in _ACK_PATTERNS or len(msg) < 5:
        return "simple"

    # Trivial factual patterns (math, definitions, lookups)
    if len(msg) < 200:
        for r in _TRIVIAL_RE:
            if r.search(msg):
                return "simple"

    # Short HAIKU_PATTERNS acks
    if len(msg_lower) < 20 and any(p in msg_lower for p in HAIKU_PATTERNS):
        return "simple"

    # --- Complex ---
    # Keyword signals
    if any(signal in msg_lower for signal in _COMPLEX_SIGNALS):
        return "complex"

    # Structural signals (multi-part, code blocks, numbered lists)
    for r in _COMPLEX_RE:
        if r.search(msg):
            return "complex"

    # Long messages with questions tend to be complex
    if len(msg) > 500 and msg.count("?") >= 2:
        return "complex"

    # Very long messages (pasting code, detailed asks)
    if len(msg) > 1000:
        return "complex"

    # --- Medium (default) ---
    return "medium"


def route(message: str) -> Route:
    # 1. Explicit named shortcuts (simple form)
    for prefix, (agent, model) in PREFIX_AGENT_SHORTCUTS.items():
        if message.startswith(prefix):
            body = message[len(prefix):]
            return Route(agent, model, body, f"prefix `{prefix.strip()}`",
                         classify_complexity(body))

    # 1b. Generic `!agent:model body` form
    m = GENERIC_PREFIX.match(message)
    if m:
        agent = m.group(1).lower()
        model = m.group(2)
        body = m.group(3)
        if agent in {"claude", "codex", "opencode", "ollama", "gemini"}:
            return Route(agent, model, body, f"prefix `!{agent}`",
                         classify_complexity(body))

    # 2. Classify complexity
    complexity = classify_complexity(message)

    # 3. Check usage — under threshold = opus for everything (except acks)
    if not is_conserving():
        # Acks still go to gemini (free, no point wasting opus on "ok")
        if complexity == "simple":
            msg_lower = message.lower().strip()
            if msg_lower in _ACK_PATTERNS or any(p in msg_lower for p in HAIKU_PATTERNS):
                return Route("gemini", None, message,
                             "simple/ack → gemini", "simple")
        # Everything else → opus
        return Route("claude", DEFAULT_MODEL_FULL, message,
                     f"default (usage <{USAGE_THRESHOLD:.0%})", complexity)

    # 4. Conserving mode: tiered routing
    # Simple: split between ollama (factual) and gemini (conversational acks)
    if complexity == "simple":
        msg_lower = message.lower().strip()
        if msg_lower in _ACK_PATTERNS or any(p in msg_lower for p in HAIKU_PATTERNS):
            return Route("gemini", None, message,
                         "conserving: simple/ack → gemini", "simple")
        return Route("ollama", None, message,
                     "conserving: simple/factual → ollama", "simple")

    # Medium/Complex: use model tier mapping
    agent, model = MODEL_TIERS[complexity]
    return Route(agent, model, message,
                 f"conserving: {complexity} → {agent}" + (f" ({model})" if model else ""),
                 complexity)

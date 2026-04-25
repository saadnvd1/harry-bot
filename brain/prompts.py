"""Shared system prompt — reads soul/*.md files + dynamic integration docs.

`get_system_prompt()` assembles the full prompt at invoke time:
soul markdown files joined in order, then integration addendum appended.

Edit personality in soul/SOUL.md, not here. This file is plumbing.
"""

from __future__ import annotations

import logging
import socket

from config import DATA_DIR
from integrations import get_prompt_addendum

logger = logging.getLogger(__name__)

_SOUL_DIR = DATA_DIR / "soul"

# Read order matters — personality first, operational rules last.
_SOUL_FILES = [
    "SOUL.md",      # personality, voice, anti-patterns
    "USER.md",      # who the owner is, trust boundaries
    "AGENTS.md",    # execution rules, reference files
    "TOOLS.md",     # tool guidance, git workflow
]


def _load_soul() -> str:
    """Read and join soul/*.md files. Crashes on missing file — intentional."""
    parts = []
    for name in _SOUL_FILES:
        path = _SOUL_DIR / name
        content = path.read_text().strip()
        if not content:
            logger.warning("soul/%s is empty", name)
            continue
        parts.append(content)
    return "\n\n".join(parts)


# Eager-load once at import time (same behavior as the old string literal).
# Edit the markdown files, restart the bot.
BASE_PROMPT = _load_soul()


def get_system_prompt(persona_name: str | None = None) -> str:
    """Build the full system prompt at invocation time. Includes integrations
    whose env vars are configured; silently omits the rest.
    When persona_name is set, appends the persona's domain prompt + user data."""
    runtime_header = f"[Runtime: host={socket.gethostname()}. Local commands run locally — do not ssh to this host.]\n\n"
    prompt = runtime_header + BASE_PROMPT + get_prompt_addendum()
    if persona_name:
        from brain.personas import get_persona
        persona = get_persona(persona_name)
        if persona:
            prompt += f"\n\n## Active Persona: {persona.command}\n\n{persona.prompt}"
            if persona.data:
                prompt += f"\n\n## User's {persona.command} context\n\n{persona.data}"
    return prompt


# Back-compat: some callers still import SYSTEM_PROMPT as a constant.
SYSTEM_PROMPT = BASE_PROMPT

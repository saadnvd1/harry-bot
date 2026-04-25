"""Persona loader — reads markdown files from `personas/` with YAML-ish frontmatter
and exposes them as sticky multi-turn expert modes.

Persona file format:
    ---
    command: travel
    description: Travel agent — research flights, hotels, build itineraries
    greeting: "Travel mode activated. Where are we headed?"
    model: claude-sonnet-4-6
    ---
    The persona prompt body (domain expertise instructions).

Optional data file at `personas/data/{command}.md` provides user-specific context
(preferences, saved research, etc.) that gets appended to the persona prompt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from brain.skills import _parse_frontmatter
from config import DATA_DIR as _DATA_DIR

logger = logging.getLogger(__name__)

PERSONAS_DIR = _DATA_DIR / "personas"
PERSONAS_DATA_DIR = PERSONAS_DIR / "data"


@dataclass
class Persona:
    command: str
    description: str
    prompt: str
    data: str | None = None
    greeting: str | None = None
    model: str | None = None
    path: Path | None = None


_personas: list[Persona] = []


def load_personas() -> list[Persona]:
    global _personas
    if not PERSONAS_DIR.exists():
        return []
    personas: list[Persona] = []
    for path in sorted(PERSONAS_DIR.glob("*.md")):
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            cmd = meta.get("command") or path.stem
            if not body.strip():
                logger.warning("persona %s has empty body", path.name)
                continue
            # Load optional user data file
            data_path = PERSONAS_DATA_DIR / f"{cmd}.md"
            data = None
            if data_path.exists():
                data = data_path.read_text(encoding="utf-8").strip() or None
            personas.append(Persona(
                command=cmd,
                description=meta.get("description", ""),
                prompt=body.rstrip(),
                data=data,
                greeting=meta.get("greeting") or None,
                model=meta.get("model") or None,
                path=path,
            ))
        except Exception as e:
            logger.warning("failed to load persona %s: %s", path, e)
    _personas = personas
    logger.info("loaded %d personas: %s", len(personas), [p.command for p in personas])
    return personas


def get_persona(name: str) -> Persona | None:
    for p in _personas:
        if p.command == name:
            return p
    return None

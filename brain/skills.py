"""Skill loader — reads markdown files from `skills/` with YAML-ish frontmatter
and exposes them as slash-command-triggered prompts.

Skill file format:
    ---
    command: email-today
    description: Summarize today's unread Gmail
    model: claude-haiku-4-5-20251001   # optional
    agent: claude                       # optional (default claude)
    ---
    The prompt body. Use {args} to inject whatever the user typed after the command.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from config import DATA_DIR

logger = logging.getLogger(__name__)

SKILLS_DIR = DATA_DIR / "skills"


@dataclass
class Skill:
    command: str
    description: str
    prompt: str
    model: str | None = None
    agent: str = "claude"
    path: Path | None = None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, flags=re.DOTALL)
    if not m:
        return {}, text
    head, body = m.group(1), m.group(2)
    meta: dict = {}
    for line in head.splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body.lstrip("\n")


def load_skills() -> list[Skill]:
    if not SKILLS_DIR.exists():
        return []
    skills: list[Skill] = []
    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            cmd = meta.get("command") or path.stem
            if not body.strip():
                logger.warning("skill %s has empty body", path.name)
                continue
            skills.append(Skill(
                command=cmd,
                description=meta.get("description", ""),
                prompt=body.rstrip(),
                model=meta.get("model") or None,
                agent=meta.get("agent", "claude"),
                path=path,
            ))
        except Exception as e:
            logger.warning("failed to load skill %s: %s", path, e)
    logger.info("loaded %d skills: %s", len(skills), [s.command for s in skills])
    return skills


def render_prompt(skill: Skill, args: str) -> str:
    if "{args}" in skill.prompt:
        return skill.prompt.replace("{args}", args.strip())
    if args.strip():
        return f"{skill.prompt}\n\nExtra input from user: {args.strip()}"
    return skill.prompt

"""Integration manifest loader.

Each subdirectory with a `manifest.json` is an integration. Manifest schema:

    {
      "name": "gmail",
      "description": "...",
      "type": "python-tool" | "mcp",
      "entry": "integrations.gmail.cli",        // python-tool only
      "server_cmd": "npx @gongrzhe/server-gmail-autoauth-mcp",  // mcp only
      "commands": [{"usage": "...", "description": "..."}, ...],
      "env_required": ["GMAIL_USER", "GMAIL_APP_PASSWORD"],
      "prompt_doc": "one-liner injected into Harry's system prompt"
    }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
INTEGRATIONS_DIR = Path(__file__).parent


@dataclass
class Integration:
    name: str
    description: str
    type: str  # "python-tool" | "mcp"
    entry: str | None = None
    server_cmd: str | None = None
    commands: list[dict] = field(default_factory=list)
    env_required: list[str] = field(default_factory=list)
    prompt_doc: str = ""
    path: Path | None = None

    @property
    def env_ok(self) -> bool:
        return all(os.environ.get(v) for v in self.env_required)

    @property
    def missing_env(self) -> list[str]:
        return [v for v in self.env_required if not os.environ.get(v)]

    @property
    def cli_path(self) -> str | None:
        """Absolute path to the Python module's CLI, for Claude to Bash-invoke."""
        if self.type != "python-tool" or not self.entry:
            return None
        # entry like "integrations.gmail.cli" → integrations/gmail/cli.py
        parts = self.entry.split(".")
        p = INTEGRATIONS_DIR.parent
        for part in parts:
            p = p / part
        return str(p.with_suffix(".py"))


_cache: list[Integration] | None = None


def load_integrations(force: bool = False) -> list[Integration]:
    global _cache
    if _cache is not None and not force:
        return _cache
    found: list[Integration] = []
    for child in sorted(INTEGRATIONS_DIR.iterdir()):
        manifest = child / "manifest.json"
        if not (child.is_dir() and manifest.exists()):
            continue
        try:
            data = json.loads(manifest.read_text())
            found.append(Integration(
                name=data["name"],
                description=data.get("description", ""),
                type=data.get("type", "python-tool"),
                entry=data.get("entry"),
                server_cmd=data.get("server_cmd"),
                commands=data.get("commands", []),
                env_required=data.get("env_required", []),
                prompt_doc=data.get("prompt_doc", ""),
                path=child,
            ))
        except Exception as e:
            logger.warning("failed to load integration manifest %s: %s", manifest, e)
    _cache = found
    logger.info("loaded %d integrations: %s", len(found), [i.name for i in found])
    return found


def get_integration(name: str) -> Integration | None:
    for i in load_integrations():
        if i.name == name:
            return i
    return None


def get_prompt_addendum() -> str:
    """Returns the text block appended to Harry's system prompt at invoke time.
    Only includes integrations whose required env vars are set.
    """
    ints = load_integrations()
    usable = [i for i in ints if i.env_ok and i.prompt_doc]
    if not usable:
        return ""
    lines = ["", "## External Tools (call via Bash)"]
    for i in usable:
        lines.append(f"- **{i.name}** — {i.prompt_doc}")
    lines.append("")
    return "\n".join(lines)

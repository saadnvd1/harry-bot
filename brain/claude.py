"""Script shortcuts + routing. Claude invocation itself lives in
agents/claude.py (driven by the worker); this module is just the fast-path
heuristics for bot-side routing.
"""

from __future__ import annotations

import json
import logging
import subprocess

from config import BOT_HOME, DATA_DIR, Config

# Re-export for backwards compat with callers that still `from brain.claude import SYSTEM_PROMPT`
from brain.prompts import SYSTEM_PROMPT  # noqa: F401

logger = logging.getLogger(__name__)

_config = Config()
_H = str(BOT_HOME)
_V = str(_config.VAULT_PATH)
_MAC = _config.MAC_IP

# Built-in shortcuts — generic, ship with the engine.
_DEFAULT_SHORTCUTS = {
    "service status": "sm list",
    "services": "sm list",
    "what's running": "sm list",
    "sm status": "sm list",

    "disk space": "df -h /",
    "uptime": "uptime",
    "free memory": "free -h",
    "memory usage": "free -h",
    "who is running": "ps aux --sort=-%mem | head -15",
    "top processes": "ps aux --sort=-%cpu | head -15",
    "cpu usage": "top -bn1 | head -15",

    "git status harry": f"cd {_H} && git status",
    "git status vault": f"cd {_V} && git status",

    "tailscale status": "tailscale status",
    "ts status": "tailscale status",
    "ping mac": f"ping -c 2 {_MAC}",
    "is mac online": f"ping -c 1 {_MAC} && echo 'Mac is online' || echo 'Mac is offline'",

    "harry logs": f"tail -30 {_H}/harry.log",
    "my logs": f"tail -30 {_H}/harry.log",
    "bot logs": f"tail -30 {_H}/harry.log",
    "worker logs": f"tail -30 {_H}/worker.log",

    "gratitude streak": f"python3 {_H}/tools/gratitude.py streak",
    "gratitude list": f"python3 {_H}/tools/gratitude.py list --days 7",
    "my gratitude": f"python3 {_H}/tools/gratitude.py list --days 7",

    "usage": f"python3 {_H}/tools/usage.py",
    "claude usage": f"python3 {_H}/tools/usage.py",
    "quota": f"python3 {_H}/tools/usage.py",
    "status": f"python3 {_H}/tools/usage.py",

    "date": "date",
    "time": "date +%H:%M",
    "today": "date '+%Y-%m-%d %A'",
}


def _load_shortcuts() -> dict[str, str]:
    """Load built-in shortcuts + optional personal extras from DATA_DIR/shortcuts.json."""
    shortcuts = dict(_DEFAULT_SHORTCUTS)
    extras_file = DATA_DIR / "shortcuts.json"
    if extras_file.exists():
        try:
            extras = json.loads(extras_file.read_text())
            shortcuts.update(extras)
            logger.info("loaded %d extra shortcuts from %s", len(extras), extras_file)
        except Exception as e:
            logger.warning("failed to load shortcuts.json: %s", e)
    return shortcuts


SCRIPT_SHORTCUTS = _load_shortcuts()

HAIKU_PATTERNS = [
    "yes", "no", "ok", "okay", "thanks", "thank you", "got it", "sure",
    "good", "nice", "cool", "great", "perfect", "sounds good", "yep", "nah",
]


def _normalize(message: str) -> str:
    return message.lower().strip().rstrip("?.!,;:").strip()


def _match_shortcut(message: str) -> str | None:
    """Return the shortcut key if the whole message is a shortcut, else None."""
    msg = _normalize(message)
    return msg if msg in SCRIPT_SHORTCUTS else None


def classify_query(message: str) -> str:
    if _match_shortcut(message):
        return "script"
    msg_lower = message.lower().strip()
    if len(msg_lower) < 20 and any(p in msg_lower for p in HAIKU_PATTERNS):
        return "haiku"
    return "sonnet"


def run_script(message: str) -> str | None:
    key = _match_shortcut(message)
    if not key:
        return None
    cmd = SCRIPT_SHORTCUTS[key]
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        output = r.stdout.strip() or r.stderr.strip() or "(no output)"
        return f"```\n{output}\n```"
    except Exception as e:
        return f"Error running command: {e}"

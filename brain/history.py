"""Conversation history — shared between bot and worker processes.

Storage: daily JSON files in vault/conversations/{YYYY-MM-DD}.json.
Structure: {user_id: [{role, content, ts}]}

Uses fcntl advisory locks for cross-process safety. Single-user bot, so
contention is rare but possible (bot writes user msg + worker writes
assistant msg simultaneously).
"""

from __future__ import annotations

import fcntl
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)
config = Config()

CONVERSATIONS_DIR = config.VAULT_PATH / "conversations"


def _history_file(date_str: str | None = None) -> Path:
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    return CONVERSATIONS_DIR / f"{date_str}.json"


def load_recent(user_id: int, max_messages: int | None = None) -> list[dict]:
    """Load last N messages for a user across the last 2 days."""
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    limit = max_messages or config.MAX_HISTORY_MESSAGES
    today = datetime.now()
    merged: list[dict] = []
    for days_ago in range(2, -1, -1):  # oldest → newest
        date_str = (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        fpath = _history_file(date_str)
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text() or "{}")
            msgs = data.get(str(user_id)) or []
            # Skip private messages — they're ephemeral, not injected as context
            merged.extend(m for m in msgs if not m.get("private"))
        except Exception as e:
            logger.error("history load %s: %s", date_str, e)
    return merged[-limit:]


def append(user_id: int, role: str, content: str, private: bool = False, interrupted: bool = False) -> None:
    """Append a single message under today's file. fcntl-locked.

    private=True marks the entry so dream consolidation and context loading
    will skip it — ephemeral conversations that shouldn't be remembered.

    interrupted=True marks a partial assistant turn that was cancelled mid-stream.
    Dream consolidation skips it (don't extract facts from half-finished thoughts),
    but context loading keeps it so the next turn sees what was tried.
    """
    CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    fpath = _history_file()
    entry: dict = {"role": role, "content": content, "ts": datetime.now().isoformat()}
    if private:
        entry["private"] = True
    if interrupted:
        entry["interrupted"] = True

    try:
        with open(fpath, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read()
                data = json.loads(raw) if raw.strip() else {}
                data.setdefault(str(user_id), []).append(entry)
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(data, indent=2))
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        logger.error("history append failed: %s", e)


def format_recent(user_id: int, max_turns: int = 10) -> str:
    msgs = load_recent(user_id, max_messages=max_turns * 2)
    if not msgs:
        return "No recent conversation."
    lines = []
    for m in msgs[-max_turns * 2:]:
        role = "User" if m["role"] == "user" else "Harry"
        lines.append(f"{role}: {m['content']}")
    return "\n".join(lines)

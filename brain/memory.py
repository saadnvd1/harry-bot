"""Harry's memory management. Reads/writes markdown files in the vault."""

import os
import subprocess
from datetime import datetime
from pathlib import Path
from config import Config

config = Config()


def get_recent_memories(n: int = 5, max_chars: int = 0) -> str:
    """Get the N most recent memory files, sorted by date."""
    mem_path = config.memory_path
    if not mem_path.exists():
        return "No memories yet."

    files = sorted(
        [f for f in mem_path.iterdir() if f.suffix == ".md" and f.name != "README.md"],
        key=lambda f: f.name,
        reverse=True,
    )[:n]

    if not files:
        return "No memories yet."

    parts = []
    for f in files:
        content = f.read_text(encoding="utf-8").strip()
        if max_chars and len(content) > max_chars:
            content = content[:max_chars] + "..."
        parts.append(f"**{f.stem}**\n{content}")

    return "\n\n".join(parts)


def get_relevant_memories(message: str, n: int = 3, max_chars: int = 300) -> str:
    """Get memories relevant to the current message via keyword scoring."""
    import re

    mem_path = config.memory_path
    if not mem_path.exists():
        return "No memories yet."

    # Extract keywords from message
    stop = {"the", "a", "an", "is", "are", "was", "were", "be", "have", "has",
            "do", "does", "did", "will", "would", "could", "should", "can",
            "i", "me", "my", "you", "your", "we", "they", "what", "how",
            "when", "where", "why", "who", "that", "this", "to", "of", "in",
            "for", "on", "with", "at", "by", "and", "but", "or", "not", "just"}
    words = re.findall(r'\w+', message.lower())
    keywords = [w for w in words if w not in stop and len(w) > 2]

    if not keywords:
        return get_recent_memories(n, max_chars)

    files = [f for f in mem_path.iterdir() if f.suffix == ".md" and f.name != "README.md"]
    if not files:
        return "No memories yet."

    # Score each file by keyword relevance
    scored = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        name_lower = f.name.lower()
        content_lower = content.lower()
        score = 0
        for kw in keywords:
            if kw in name_lower:
                score += 3
            score += content_lower.count(kw)
        # Recency bonus: newer files get a small boost
        score += 0.1  # base score so all files are eligible
        scored.append((score, f, content))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:n]

    if not top or all(s[0] < 0.5 for s in top):
        return get_recent_memories(n, max_chars)

    parts = []
    for _score, f, content in top:
        if max_chars and len(content) > max_chars:
            content = content[:max_chars] + "..."
        parts.append(f"**{f.stem}**\n{content}")

    return "\n\n".join(parts)


def save_memory(topic: str, content: str) -> str:
    """Save a memory entry. Returns the file path."""
    mem_path = config.memory_path
    mem_path.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    # Clean topic for filename
    clean_topic = topic.lower().replace(" ", "-")[:50]
    filename = f"{date_str}_{clean_topic}.md"
    filepath = mem_path / filename

    # Append if same topic today, otherwise create
    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        filepath.write_text(f"{existing}\n\n---\n\n{content}", encoding="utf-8")
    else:
        filepath.write_text(content, encoding="utf-8")

    # Git commit the memory
    _git_commit_vault(f"harry memory: {topic}")

    return str(filepath)


def delete_memory(search: str) -> str:
    """Delete memory files matching a search term."""
    mem_path = config.memory_path
    deleted = []
    for f in mem_path.iterdir():
        if f.suffix == ".md" and search.lower() in f.name.lower():
            f.unlink()
            deleted.append(f.name)

    if deleted:
        _git_commit_vault(f"harry forget: {search}")
        return f"Deleted: {', '.join(deleted)}"
    return "No matching memories found."


def save_conversation_summary(summary: str) -> str:
    """Save a compressed conversation summary."""
    conv_path = config.conversations_path
    conv_path.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
    filepath = conv_path / f"{date_str}.md"
    filepath.write_text(summary, encoding="utf-8")

    _git_commit_vault(f"conversation: {date_str}")
    return str(filepath)


def _git_commit_vault(message: str):
    """Auto-commit vault changes."""
    vault = str(config.VAULT_PATH)
    try:
        subprocess.run(["git", "add", "-A"], cwd=vault, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=vault, capture_output=True, timeout=10,
        )
    except Exception:
        pass  # Non-critical, don't crash the bot

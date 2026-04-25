"""Dream consolidation — background memory consolidation.

Phase 1: LLM reads recent conversations + existing memories, extracts atomic
         facts, flags duplicates/stale content. Output is structured lines.
Phase 2: Python parses Phase 1 output and applies changes — writes memory
         files, updates profile, removes stale entries.

Runs as a scheduled proactive job (every 4 hours). Uses haiku for cost
efficiency — this is background housekeeping, not conversation.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)
config = Config()

from config import DATA_DIR
TEMPLATES_DIR = DATA_DIR / "context"
DREAM_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / ".dream_cursor"


def run_dream() -> str | None:
    """Run dream consolidation. Returns summary or None if nothing to process."""
    conversations = _load_recent_conversations(days=2)
    if not conversations:
        logger.info("dream: no recent conversations to process")
        return None

    # Check cursor — skip already-processed conversations
    last_cursor = _get_cursor()
    latest_mtime = _latest_conversation_mtime(days=2)
    if last_cursor and latest_mtime and latest_mtime <= last_cursor:
        logger.info("dream: conversations already processed (cursor=%s)", last_cursor)
        return None

    existing_memories = _load_existing_memories()
    profile = _load_profile()

    # Phase 1: LLM extracts facts
    phase1_prompt = _build_phase1_prompt(conversations, existing_memories, profile)
    logger.info("dream phase 1: analyzing %d chars of conversation", len(conversations))
    analysis = _run_claude(phase1_prompt)

    if not analysis or "[SKIP]" in analysis:
        logger.info("dream phase 1: nothing to consolidate")
        _set_cursor(latest_mtime)
        return None

    # Phase 2: Python applies the structured output
    findings = _parse_findings(analysis)
    logger.info(
        "dream phase 2: %d memories, %d profile updates, %d removals",
        len(findings["memory"]), len(findings["profile"]), len(findings["remove"]),
    )

    _apply_memories(findings["memory"])
    _apply_profile_updates(findings["profile"])
    _apply_removals(findings["remove"])

    _git_commit_vault(
        f"dream: {len(findings['memory'])}m {len(findings['profile'])}p {len(findings['remove'])}r"
    )
    _set_cursor(latest_mtime)

    summary = (
        f"Dream complete: {len(findings['memory'])} memories, "
        f"{len(findings['profile'])} profile updates, "
        f"{len(findings['remove'])} removals"
    )
    logger.info(summary)
    return summary


# --- Phase 1: LLM analysis ---

def _build_phase1_prompt(conversations: str, memories: str, profile: str) -> str:
    template_path = TEMPLATES_DIR / "dream_phase1.md"
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        conversations=conversations,
        existing_memories=memories,
        profile=profile,
    )


def _run_claude(prompt: str) -> str:
    """Run Claude CLI (haiku) and return the response."""
    cmd = [
        "claude", "--print",
        "--model", "claude-haiku-4-5-20251001",
        "--permission-mode", "bypassPermissions",
        prompt,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=_claude_env(),
        )
        if result.returncode != 0:
            logger.warning("dream claude exited %d: %s", result.returncode, result.stderr[:200])
            return ""
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.warning("dream: claude timed out")
        return ""
    except Exception as e:
        logger.exception("dream failed: %s", e)
        return ""


# --- Phase 2: Python applies changes ---

def _parse_findings(analysis: str) -> dict[str, list[str]]:
    """Parse Phase 1 output into categorized findings."""
    findings: dict[str, list[str]] = {"memory": [], "profile": [], "remove": []}
    for line in analysis.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("[MEMORY]"):
            fact = line[len("[MEMORY]"):].strip()
            if fact:
                findings["memory"].append(fact)
        elif line.startswith("[PROFILE]"):
            fact = line[len("[PROFILE]"):].strip()
            if fact:
                findings["profile"].append(fact)
        elif line.startswith("[REMOVE]"):
            fact = line[len("[REMOVE]"):].strip()
            if fact:
                findings["remove"].append(fact)
    return findings


def _apply_memories(facts: list[str]):
    """Write new memory facts to a single consolidated file for today."""
    if not facts:
        return

    mem_path = config.memory_path
    mem_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    filepath = mem_path / f"{today}_dream-consolidation.md"

    content = f"# Dream Consolidation — {today}\n\n"
    for fact in facts:
        content += f"- {fact}\n"

    if filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        # Append new facts, avoiding exact duplicates
        for fact in facts:
            if fact not in existing:
                existing += f"- {fact}\n"
        filepath.write_text(existing, encoding="utf-8")
    else:
        filepath.write_text(content, encoding="utf-8")

    logger.info("dream: wrote %d facts to %s", len(facts), filepath.name)


def _apply_profile_updates(updates: list[str]):
    """Append profile updates to a notes section in profile.md."""
    if not updates:
        return

    profile_path = config.about_path / "profile.md"
    if not profile_path.exists():
        return

    profile = profile_path.read_text(encoding="utf-8")

    # Add or append to a "## Harry's Observations" section
    section_header = "## Harry's Observations"
    today = datetime.now().strftime("%Y-%m-%d")

    new_entries = "\n".join(f"- [{today}] {u}" for u in updates)

    if section_header in profile:
        # Append to existing section
        profile = profile.replace(
            section_header,
            f"{section_header}\n{new_entries}",
        )
    else:
        profile = profile.rstrip() + f"\n\n{section_header}\n{new_entries}\n"

    profile_path.write_text(profile, encoding="utf-8")
    logger.info("dream: %d profile updates applied", len(updates))


def _apply_removals(removals: list[str]):
    """Remove stale memory files or content matching removal descriptions."""
    if not removals:
        return

    mem_path = config.memory_path
    if not mem_path.exists():
        return

    removed_count = 0
    for removal in removals:
        # Try to extract a filename from "filename: reason" format
        match = re.match(r"^([\w\-\.]+\.md)\s*:", removal)
        if match:
            filename = match.group(1)
            target = mem_path / filename
            if target.exists():
                target.unlink()
                removed_count += 1
                logger.info("dream: removed %s", filename)

    if removed_count:
        logger.info("dream: removed %d stale memory files", removed_count)


# --- Conversation loading ---

def _load_recent_conversations(days: int = 2) -> str:
    """Load conversation JSON files from the last N days as readable text."""
    conv_dir = config.conversations_path
    if not conv_dir.exists():
        return ""

    parts = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = conv_dir / f"{date}.json"
        if not filepath.exists():
            continue

        try:
            data = json.loads(filepath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        messages = []
        for _uid, msgs in data.items():
            for m in msgs:
                # Skip private messages — ephemeral, not for memory consolidation.
                # Skip interrupted partials — half-finished thoughts shouldn't become "facts".
                if m.get("private") or m.get("interrupted"):
                    continue
                role = m.get("role", "unknown")
                text = (m.get("content") or m.get("text") or "").strip()
                ts = m.get("ts", "")
                if text:
                    messages.append({"ts": ts, "role": role, "text": text})

        messages.sort(key=lambda m: m["ts"])

        if messages:
            parts.append(f"### {date}")
            for m in messages:
                hour = ""
                if m["ts"]:
                    try:
                        hour = datetime.fromisoformat(m["ts"]).strftime("%H:%M")
                    except Exception:
                        pass
                role_label = "User" if m["role"] == "user" else "Harry"
                text = m["text"][:500]
                parts.append(f"[{hour}] {role_label}: {text}")
            parts.append("")

    return "\n".join(parts)


def _load_existing_memories() -> str:
    mem_path = config.memory_path
    if not mem_path.exists():
        return "No existing memories."

    files = sorted(
        [f for f in mem_path.iterdir() if f.suffix == ".md" and f.name != "README.md"],
        key=lambda f: f.name,
        reverse=True,
    )[:20]

    if not files:
        return "No existing memories."

    parts = []
    for f in files:
        content = f.read_text(encoding="utf-8").strip()[:500]
        parts.append(f"**{f.name}**\n{content}")
    return "\n\n".join(parts)


def _load_profile() -> str:
    profile_path = config.about_path / "profile.md"
    if profile_path.exists():
        return profile_path.read_text(encoding="utf-8")
    return "No profile found."


# --- Utilities ---

def _claude_env():
    import os
    return {**os.environ, "IS_SANDBOX": "1"}


def _git_commit_vault(message: str):
    vault = str(config.VAULT_PATH)
    try:
        subprocess.run(["git", "add", "-A"], cwd=vault, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=vault, capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _latest_conversation_mtime(days: int = 2) -> float | None:
    conv_dir = config.conversations_path
    if not conv_dir.exists():
        return None

    latest = None
    for i in range(days):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        filepath = conv_dir / f"{date}.json"
        if filepath.exists():
            mtime = filepath.stat().st_mtime
            if latest is None or mtime > latest:
                latest = mtime
    return latest


def _get_cursor() -> float | None:
    if DREAM_STATE_FILE.exists():
        try:
            return float(DREAM_STATE_FILE.read_text().strip())
        except (ValueError, OSError):
            pass
    return None


def _set_cursor(mtime: float | None):
    if mtime is None:
        return
    DREAM_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    DREAM_STATE_FILE.write_text(str(mtime))

"""Proactive scheduled messages — briefings, check-ins, reflection.

All Claude-backed jobs now enqueue into the worker queue. vault_git_push and
the Python-based conversation summarizer still run inline.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import time, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from config import Config
from worker import queue

logger = logging.getLogger(__name__)
config = Config()

REFLECTION_STATE_FILE = ".last_reflection"


def setup_jobs(job_queue, _config: Config):
    tz = ZoneInfo(_config.TIMEZONE)

    # TEMP DISABLED: morning briefing + evening checkin
    # job_queue.run_daily(
    #     morning_briefing,
    #     time=time(hour=_config.MORNING_BRIEFING_HOUR, minute=0, tzinfo=tz),
    #     name="morning_briefing",
    # )
    # job_queue.run_daily(
    #     evening_checkin,
    #     time=time(hour=21, minute=0, tzinfo=tz),
    #     name="evening_checkin",
    # )
    job_queue.run_repeating(
        vault_git_push, interval=6 * 3600, first=60, name="vault_git_push",
    )
    job_queue.run_repeating(
        idle_reflection, interval=1800, first=1800, name="idle_reflection",
    )
    job_queue.run_repeating(
        dream_consolidation, interval=4 * 3600, first=3600, name="dream_consolidation",
    )

    logger.info(
        "Proactive jobs registered: briefing %d:00, check-in 21:00, vault push 6h, reflection 30m, dream 4h",
        _config.MORNING_BRIEFING_HOUR,
    )


async def morning_briefing(context: ContextTypes.DEFAULT_TYPE):
    job_id = queue.enqueue(
        kind="briefing",
        payload={"user_id": config.ALLOWED_USER_ID, "chat_id": config.ALLOWED_USER_ID},
        stream_chat_id=config.ALLOWED_USER_ID,
    )
    logger.info("enqueued briefing job %d", job_id)


async def evening_checkin(context: ContextTypes.DEFAULT_TYPE):
    job_id = queue.enqueue(
        kind="checkin",
        payload={"user_id": config.ALLOWED_USER_ID, "chat_id": config.ALLOWED_USER_ID},
        stream_chat_id=config.ALLOWED_USER_ID,
    )
    logger.info("enqueued checkin job %d", job_id)


async def idle_reflection(context: ContextTypes.DEFAULT_TYPE):
    """Python summarizer always runs. Claude reflection only for heavy days (20+ msgs)."""
    now = datetime.now().timestamp()

    today = datetime.now().strftime("%Y-%m-%d")
    convo_file = config.VAULT_PATH / "conversations" / f"{today}.json"
    if not convo_file.exists():
        return

    try:
        data = json.loads(convo_file.read_text())
        msg_count = sum(len(v) for v in data.values())
    except Exception:
        return
    if msg_count < 4:
        return

    idle_time = now - convo_file.stat().st_mtime
    if idle_time < 1800 or idle_time > 7200:
        return

    convo_mtime = convo_file.stat().st_mtime
    state_dir = Path(__file__).resolve().parent.parent / "data"
    state_file = state_dir / REFLECTION_STATE_FILE
    if state_file.exists():
        try:
            last = json.loads(state_file.read_text())
            if last.get("date") == today and last.get("convo_mtime") == convo_mtime:
                return
        except Exception:
            pass

    logger.info("reflection: %d msgs, %.0f min idle", msg_count, idle_time / 60)

    summary = _summarize_conversation(data, today)
    memory_dir = config.VAULT_PATH / "harry-memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / f"{today}_conversation-summary.md").write_text(summary)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"date": today, "convo_mtime": convo_mtime}))

    _run(
        f"cd {config.VAULT_PATH} && git add -A && git commit -m 'harry reflection: {today}'",
        timeout=15,
    )

    logger.info("reflection python summary saved (%d chars)", len(summary))

    if msg_count >= 20:
        date = datetime.now().strftime("%Y-%m-%d %A")
        prompt = (
            f"[Date: {date}]\n"
            f"[Today's conversation summary (already saved):\n{summary}\n]\n\n"
            "You just reflected on a longer-than-usual conversation.\n"
            "Based on the summary above, briefly note:\n"
            "1. Any emotional undertones or concerns\n"
            "2. Action items the user might forget\n"
            "3. One idea for how you could serve them better\n"
            "Keep response under 100 words — this is just for my logs."
        )
        queue.enqueue(
            kind="reflection",
            payload={
                "user_id": config.ALLOWED_USER_ID,
                "chat_id": config.ALLOWED_USER_ID,
                "prompt": prompt,
                "model": "claude-haiku-4-5-20251001",
            },
            stream_chat_id=config.ALLOWED_USER_ID,
        )
        logger.info("reflection claude job enqueued (haiku)")


def _summarize_conversation(data: dict, date: str) -> str:
    """Summarize a day's conversation. Input schema: {user_id_str: [{role, content, ts}]}."""
    from datetime import datetime as _dt

    messages = []
    for _uid, msgs in data.items():
        for m in msgs:
            role = m.get("role", "unknown")
            text = (m.get("content") or m.get("text") or "")[:200]
            hour = _hour_from_ts(m.get("ts", ""))
            messages.append({"hour": hour, "role": role, "text": text})
    # Sort chronologically for stable output
    messages.sort(key=lambda m: m["hour"] or "")

    msg_count = len(messages)
    user_msgs = [m for m in messages if m["role"] == "user"]
    harry_msgs = [m for m in messages if m["role"] == "assistant"]

    all_text = " ".join(m["text"].lower() for m in messages)
    words = [w for w in all_text.split() if len(w) > 4 and w.isalpha()]
    word_freq: dict[str, int] = {}
    for w in words:
        word_freq[w] = word_freq.get(w, 0) + 1
    top = sorted(word_freq.items(), key=lambda x: -x[1])[:10]
    keywords = [
        w for w, _ in top
        if w not in {"about", "would", "could", "should", "there", "their",
                     "which", "these", "those", "being"}
    ][:5]

    hours = sorted({m["hour"] for m in messages if m["hour"]})
    time_range = f"{hours[0]} - {hours[-1]}" if hours else "unknown"

    lines = [
        f"# Conversation Summary: {date}",
        "",
        f"**Messages:** {msg_count} total ({len(user_msgs)} from user, {len(harry_msgs)} from Harry)",
        f"**Active hours:** {time_range}",
        f"**Keywords:** {', '.join(keywords) if keywords else 'none extracted'}",
        "",
        "## User Messages (truncated):",
    ]
    for m in user_msgs[:10]:
        label = m["hour"] or "?"
        lines.append(f"- [{label}] {m['text'][:100]}...")
    if len(user_msgs) > 10:
        lines.append(f"- ... and {len(user_msgs) - 10} more")
    return "\n".join(lines)


def _hour_from_ts(ts: str) -> str:
    """Extract 'HH:MM' from an ISO timestamp. Empty string if unparseable."""
    if not ts:
        return ""
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except Exception:
        return ""


async def dream_consolidation(context: ContextTypes.DEFAULT_TYPE):
    """Run dream consolidation — extract memories from recent conversations."""
    try:
        from brain.dream import run_dream
        summary = run_dream()
        if summary:
            logger.info("dream: %s", summary)
    except Exception as e:
        logger.exception("dream consolidation failed: %s", e)


async def vault_git_push(context: ContextTypes.DEFAULT_TYPE):
    vault = str(config.VAULT_PATH)
    _run(
        f"cd {vault} && git add -A && git commit -m 'vault sync: auto' && git push",
        timeout=30,
    )
    logger.info("vault git push complete")


def _run(cmd: str, timeout: int = 10) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""

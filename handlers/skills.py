"""Register skills as Telegram slash commands. Each skill file becomes
a `/command` that enqueues a raw job with the skill's prompt template.

Some commands have "direct" fast paths — when invoked with no args (or specific
sub-args), they run a CLI script and send the output directly, skipping the LLM.
"""

from __future__ import annotations

import logging
import subprocess

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from brain.skills import Skill, load_skills, render_prompt
from channels.telegram import markdown_to_html
from config import BOT_HOME, Config
from worker import queue

logger = logging.getLogger(__name__)
config = Config()

_H = str(BOT_HOME)

# Fast paths: skill command → {args_pattern: shell_command}
# Empty string key "" means "no args". These bypass the LLM entirely.
DIRECT_COMMANDS: dict[str, dict[str, str]] = {
    "news": {
        "": f"python3 {_H}/integrations/briefings/cli.py today",
        "cnn": f"python3 {_H}/integrations/briefings/cli.py cnn",
        "hn": f"python3 {_H}/integrations/briefings/cli.py hn",
        "list": f"python3 {_H}/integrations/briefings/cli.py list",
    },
    "learn": {
        "topics": f"python3 {_H}/integrations/learning/cli.py topics",
        "note": f"python3 {_H}/integrations/learning/cli.py note",
    },
}


def _try_direct(skill_name: str, args: str) -> str | None:
    """If this skill+args combo has a direct fast path, run it and return output."""
    directs = DIRECT_COMMANDS.get(skill_name)
    if not directs:
        return None

    # Check for exact match on args (or first word of args for sub-commands)
    first_word = args.split()[0] if args else ""
    cmd = directs.get(args) or directs.get(first_word)
    if not cmd:
        return None

    # For date args like "cnn 2026-04-20", append the extra arg
    extra = args[len(first_word):].strip() if first_word else ""
    if extra:
        cmd = f"{cmd} {extra}"

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15,
        )
        return result.stdout.strip() or result.stderr.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return "(command timed out)"
    except Exception as e:
        return f"(error: {e})"


def _tg_command(name: str) -> str:
    """Telegram only allows [a-zA-Z0-9_] in commands — convert dashes."""
    return name.replace("-", "_")


def _make_handler(skill: Skill):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ALLOWED_USER_ID:
            return
        # Strip the command, keep extra args
        text = update.message.text or ""
        # Telegram sends the underscore form in the msg text
        tg_name = _tg_command(skill.command)
        prefix = f"/{tg_name}"
        args = text[len(prefix):].strip() if text.startswith(prefix) else ""

        # Try direct fast path first (no LLM)
        direct_output = _try_direct(skill.command, args)
        if direct_output is not None:
            logger.info("skill /%s → direct (args=%r)", skill.command, args[:60])
            # Split long messages (Telegram 4096 char limit)
            for i in range(0, len(direct_output), 4000):
                chunk = direct_output[i:i + 4000]
                await update.message.reply_text(chunk)
            return

        prompt = render_prompt(skill, args)
        job_id = queue.enqueue(
            kind="raw",
            payload={
                "user_id": update.effective_user.id,
                "chat_id": update.effective_chat.id,
                "prompt": prompt,
                "model": skill.model,
                "skill": skill.command,
            },
            agent=skill.agent,
            stream_chat_id=update.effective_chat.id,
        )
        logger.info("skill /%s → job %d (args=%r)", skill.command, job_id, args[:60])

    return handler


def register_skills(app: Application) -> list[Skill]:
    skills = load_skills()
    for skill in skills:
        app.add_handler(CommandHandler(_tg_command(skill.command), _make_handler(skill)))
    return skills

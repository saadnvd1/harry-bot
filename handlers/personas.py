"""Register personas as Telegram slash commands. Each persona file becomes
a `/command` that activates a sticky expert mode for subsequent chat messages.

Activation: `/travel` or `/travel plan a trip to Japan`
Deactivation: `/exit`
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from brain.personas import Persona, load_personas
from config import Config
from worker import queue

logger = logging.getLogger(__name__)
config = Config()

PERSONA_EXPIRY_SECONDS = 24 * 3600  # 24 hours


def _tg_command(name: str) -> str:
    """Telegram only allows [a-zA-Z0-9_] in commands — convert dashes."""
    return name.replace("-", "_")


def _make_activate_handler(persona: Persona):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ALLOWED_USER_ID:
            return
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        # Activate persona
        queue.set_user_state(user_id, "active_persona", persona.command)

        # Reset session so persona starts fresh
        queue.reset_session(user_id, "claude")

        logger.info("persona /%s activated for user %s", persona.command, user_id)

        # If args provided, enqueue as chat with that text
        text = update.message.text or ""
        tg_name = _tg_command(persona.command)
        prefix = f"/{tg_name}"
        args = text[len(prefix):].strip() if text.startswith(prefix) else ""

        if args:
            job_id = queue.enqueue(
                kind="chat",
                payload={
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "text": args,
                    "complexity": "medium",
                },
                agent="claude",
                stream_chat_id=chat_id,
            )
            logger.info("persona /%s → chat job %d (args=%r)", persona.command, job_id, args[:60])
        else:
            greeting = persona.greeting or f"{persona.description} — active. What do you need?"
            await update.message.reply_text(greeting)

    return handler


async def _exit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.ALLOWED_USER_ID:
        return
    user_id = update.effective_user.id
    active = queue.get_user_state(user_id, "active_persona")
    if active:
        queue.delete_user_state(user_id, "active_persona")
        queue.reset_session(user_id, "claude")
        logger.info("persona /%s deactivated for user %s", active, user_id)
        await update.message.reply_text(f"Exited {active} mode. Back to normal Harry.")
    else:
        await update.message.reply_text("No active persona to exit.")


def register_personas(app: Application) -> list[Persona]:
    personas = load_personas()
    for persona in personas:
        app.add_handler(CommandHandler(_tg_command(persona.command), _make_activate_handler(persona)))
    # Register /exit command
    app.add_handler(CommandHandler("exit", _exit_handler))
    return personas

"""Handler for /learn command — on-demand Socratic learning sessions."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import Config
from handlers.commands import auth
from brain.learn import build_learn_prompt
from worker import queue

logger = logging.getLogger(__name__)
config = Config()


@auth
async def learn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /learn [optional topic/domain]. Builds enriched prompt, enqueues."""
    text = update.message.text or ""
    # Strip /learn prefix, capture optional topic arg
    topic = text.replace("/learn", "", 1).strip() or None

    try:
        prompt, model = build_learn_prompt(config.learning_path.parent, topic)
    except Exception as e:
        logger.exception("learn prompt build failed")
        await update.message.reply_text(f"Learning session failed to start: {e}")
        return

    # Reset session so /learn starts fresh — follow-up messages will
    # continue in this new session via normal chat handler
    user_id = update.effective_user.id
    queue.reset_session(user_id, "claude")

    job_id = queue.enqueue(
        kind="chat",
        payload={
            "user_id": user_id,
            "chat_id": update.effective_chat.id,
            "text": prompt,
            "model": model,
            "complexity": "complex",  # full enrichment tier
            "skill": "learn",
        },
        agent="claude",
        stream_chat_id=update.effective_chat.id,
    )
    logger.info("/learn → job %d (topic=%r)", job_id, topic)

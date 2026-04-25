"""Callback query handlers for inline keyboard buttons."""

import logging
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

SPECTER_RESPONSE_FILE = Path("/tmp/specter-response")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route inline keyboard callbacks."""
    query = update.callback_query
    if not query:
        return

    data = query.data

    if data in ("specter_approve", "specter_skip"):
        action = "approve" if data == "specter_approve" else "skip"
        label = "Approved \u2705" if action == "approve" else "Skipped \u23ed"

        # Write signal file for Mac-side polling via SSH
        SPECTER_RESPONSE_FILE.write_text(action)

        await query.answer(f"Campaign {action}")
        try:
            await query.edit_message_text(
                text=query.message.text + f"\n\n\u2192 {label}",
            )
        except Exception as e:
            logger.warning("Failed to edit specter message: %s", e)

        logger.info("Specter campaign %s", action)
        return

    # Unknown callback
    await query.answer()

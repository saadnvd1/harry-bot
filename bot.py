"""Harry — personal AI assistant on Telegram."""

import logging
import os
import signal
import sys
import traceback
from pathlib import Path
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

# Load .env file
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from config import Config
from handlers.commands import (
    start, help_cmd, remember, forget, memory_cmd,
    run_cmd, mac_cmd, sync_cmd, projects_cmd, status_cmd,
    schedule_cmd, schedules_cmd, unschedule_cmd, integrations_cmd, agents_cmd,
    reset_cmd,
)
from handlers.chat import handle_message
from handlers.learn import learn_command
from handlers.media import handle_voice, handle_photo
from handlers.proactive import setup_jobs
from handlers.callbacks import handle_callback
from handlers.skills import register_skills
from handlers.personas import register_personas

from logging.handlers import RotatingFileHandler
LOG_FILE = Path(__file__).parent / "harry.log"
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3),
    ],
)
logger = logging.getLogger(__name__)


def signal_handler(signum, frame):
    """Log signal info before dying."""
    sig_name = signal.Signals(signum).name
    # Get stack trace to see what we were doing when killed
    stack = ''.join(traceback.format_stack(frame))
    logger.warning("CAUGHT SIGNAL %s (%d) - shutting down", sig_name, signum)
    logger.warning("Stack at signal:\n%s", stack)
    # Try to figure out who sent it (Linux only)
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        # Can't actually get sender PID from signal handler in Python easily
        # but at least we logged the signal
    except:
        pass
    sys.exit(128 + signum)


# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def main():
    config = Config()
    app = Application.builder().token(config.TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("briefing", _briefing))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(CommandHandler("run", run_cmd))
    app.add_handler(CommandHandler("mac", mac_cmd))
    app.add_handler(CommandHandler("sync", sync_cmd))
    app.add_handler(CommandHandler("projects", projects_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("schedules", schedules_cmd))
    app.add_handler(CommandHandler("unschedule", unschedule_cmd))
    app.add_handler(CommandHandler("integrations", integrations_cmd))
    app.add_handler(CommandHandler("agents", agents_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("clear", reset_cmd))
    app.add_handler(CommandHandler("learn", learn_command))

    # Callback query handler (inline keyboard buttons)
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Dynamically register skill commands from skills/*.md
    register_skills(app)

    # Register persona commands from personas/*.md
    register_personas(app)

    # Voice and photo handlers
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Catch-all for natural conversation
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Unknown command fallback
    async def unknown_cmd(update, context):
        cmd = update.message.text.split()[0]
        await update.message.reply_text(f"Unknown command: {cmd}\nType /help for available commands.")
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    # Proactive scheduled jobs
    setup_jobs(app.job_queue, config)

    logger.info("Harry is alive. Polling for messages...")
    app.run_polling(drop_pending_updates=True)


async def _briefing(update, context):
    """Manual briefing trigger — enqueues a briefing job for the worker."""
    if update.effective_user.id != Config.ALLOWED_USER_ID:
        return
    from worker import queue
    queue.enqueue(
        kind="briefing",
        payload={"user_id": Config.ALLOWED_USER_ID, "chat_id": update.effective_chat.id},
        stream_chat_id=update.effective_chat.id,
    )
    await update.message.reply_text("Briefing queued…")


if __name__ == "__main__":
    main()

"""Chat handler — thin now: enqueues a job for the worker to process.

Fast paths (script shortcuts) still execute synchronously here. Anything
that needs Claude gets enqueued so the worker can stream the response.
"""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from brain.claude import classify_query, run_script
from brain.router import route as route_message
from brain.skills import load_skills, render_prompt
from config import Config
from worker import queue

_HN_URL_RE = re.compile(r"https?://news\.ycombinator\.com/item\?id=\d+")

logger = logging.getLogger(__name__)
config = Config()


def auth_check(user_id: int) -> bool:
    return user_id == config.ALLOWED_USER_ID


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text message from Telegram."""
    user_id = update.effective_user.id
    if not auth_check(user_id):
        return

    message = update.message.text
    if not message:
        return

    await handle_message_text(update, context, message)


async def handle_message_text(update: Update, context: ContextTypes.DEFAULT_TYPE, message: str):
    """Core message handler — called directly with text (for voice transcripts too)."""
    user_id = update.effective_user.id

    logger.info(">>> User: %s", message[:100])

    # Background prefix: !bg skips cancel logic, queues without interrupting current work
    # Strip this FIRST so script shortcuts like "!bg usage" still work
    skip_cancel = False
    if message.startswith("!bg "):
        message = message[4:]
        skip_cancel = True
        logger.info("!bg prefix: skipping cancel, will queue behind current work")

    # Research prefix: !r forces complex complexity (full enrich + 20min timeout)
    # and protects the job from being cancelled by subsequent messages. Use when
    # kicking off deep research so impatient follow-ups don't kill the work.
    research_mode = False
    if message.startswith("!r "):
        message = message[3:]
        research_mode = True
        skip_cancel = True
        logger.info("!r prefix: research mode (protected from steer, 20min timeout)")

    # Private mode: session-level toggle via !private on/off, or per-message !private <msg>
    is_private = False
    if message.strip().lower() in ("!private on", "!private start"):
        queue.set_user_state(user_id, "private_mode", "1")
        logger.info("private mode: enabled for user %s", user_id)
        await update.message.reply_text("🔒 Private mode on. Messages won't be recorded until you send `!private off`.", parse_mode="HTML")
        return
    elif message.strip().lower() in ("!private off", "!private stop", "!private end"):
        queue.delete_user_state(user_id, "private_mode")
        logger.info("private mode: disabled for user %s", user_id)
        await update.message.reply_text("🔓 Private mode off. Back to normal.")
        return
    elif message.startswith("!private "):
        # Per-message override
        message = message[9:]
        is_private = True
        logger.info("!private prefix: this message will not be recorded")
    elif queue.get_user_state(user_id, "private_mode") == "1":
        # Session-level private mode active
        is_private = True
        logger.info("private mode: active for user %s, treating as private", user_id)

    # Script shortcut — synchronous, no LLM
    if classify_query(message) == "script":
        response = run_script(message)
        from brain import history
        history.append(user_id, "user", message)
        history.append(user_id, "assistant", response)
        logger.info("<<< Harry [script]: %s", response[:100])
        from channels.telegram import markdown_to_html
        await update.message.reply_text(markdown_to_html(response), parse_mode="HTML")
        return

    # Auto-detect bare HN URLs → route through /hn skill
    stripped = message.strip()
    if _HN_URL_RE.match(stripped) and "\n" not in stripped:
        skills = load_skills()
        hn_skill = next((s for s in skills if s.command == "hn"), None)
        if hn_skill:
            prompt = render_prompt(hn_skill, stripped)
            job_id = queue.enqueue(
                kind="raw",
                payload={
                    "user_id": user_id,
                    "chat_id": update.effective_chat.id,
                    "prompt": prompt,
                    "model": hn_skill.model,
                    "skill": hn_skill.command,
                    "private": is_private,
                },
                agent=hn_skill.agent,
                stream_chat_id=update.effective_chat.id,
            )
            logger.info("auto-detected HN URL → /hn skill, job %d", job_id)
            return

    # Cancel-only commands: cancel in-flight work but don't enqueue anything new.
    # Explicit cancel — include protected (research) jobs too.
    _CANCEL_WORDS = {"stop", "cancel", "abort", "nevermind", "nvm"}
    if message.strip().lower() in _CANCEL_WORDS:
        cancelled_ids = queue.request_cancel_for_user(user_id, kinds=("chat",), include_protected=True)
        if cancelled_ids:
            logger.info("cancel command: cancelled in-flight chat jobs %s", cancelled_ids)
            await update.message.reply_text("✋ Cancelled.")
        else:
            logger.info("cancel command: nothing to cancel")
            await update.message.reply_text("Nothing running to cancel.")
        return

    # Route to agent+model (prefix override or heuristic or default)
    r = route_message(message)
    logger.info("route: agent=%s model=%s complexity=%s (%s)",
                r.agent, r.model or "default", r.complexity, r.reason)

    # Steer mode: cancel any in-flight chat work for this user before enqueuing
    # (unless !bg prefix was used)
    if not skip_cancel:
        cancelled_ids = queue.request_cancel_for_user(user_id, kinds=("chat",))
        if cancelled_ids:
            logger.info("steer: cancelled in-flight chat jobs %s", cancelled_ids)

    # Research mode forces complex complexity so enrich loads full context
    # and runner uses the 20-min timeout. Also marks the job protected so
    # normal follow-up messages can't steer-cancel it.
    effective_complexity = "complex" if research_mode else r.complexity

    job_id = queue.enqueue(
        kind="chat",
        payload={
            "user_id": user_id,
            "chat_id": update.effective_chat.id,
            "text": r.message,
            "model": r.model,
            "complexity": effective_complexity,
            "private": is_private,
            "protected": research_mode,
        },
        agent=r.agent,
        stream_chat_id=update.effective_chat.id,
    )
    logger.info("enqueued chat job %d (agent=%s, complexity=%s%s)",
                job_id, r.agent, effective_complexity,
                ", protected" if research_mode else "")

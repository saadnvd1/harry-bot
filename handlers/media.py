"""Handle voice messages and photos from Telegram."""

import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from telegram import Update
from telegram.ext import ContextTypes
from config import Config
from handlers.chat import handle_message as handle_text, auth_check

logger = logging.getLogger(__name__)
config = Config()

# Lazy-load whisper model (first call takes a few seconds to download model)
_whisper_model = None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logger.info("loading whisper model (first time)...")
        _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        logger.info("whisper model loaded")
    return _whisper_model


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages: download → transcribe → pass to Claude."""
    user_id = update.effective_user.id
    if not auth_check(user_id):
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    logger.info(">>> User: [voice message, %ds]", voice.duration)
    await update.message.chat.send_action("typing")

    try:
        # Download voice file
        file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".oga", delete=False) as tmp_oga:
            await file.download_to_drive(tmp_oga.name)
            oga_path = tmp_oga.name

        # Convert to wav with ffmpeg
        wav_path = oga_path.replace(".oga", ".wav")
        subprocess.run(
            ["ffmpeg", "-i", oga_path, "-ar", "16000", "-ac", "1", wav_path, "-y"],
            capture_output=True, timeout=30,
        )

        # Transcribe with whisper
        model = _get_whisper()
        segments, _ = model.transcribe(wav_path, beam_size=5)
        transcript = " ".join(seg.text for seg in segments).strip()

        # Cleanup
        os.unlink(oga_path)
        os.unlink(wav_path)

        if not transcript:
            await update.message.reply_text("Couldn't make out what you said. Try again?")
            return

        logger.info("transcribed: %s", transcript[:100])

        # Pass transcription to chat handler directly (Message is immutable)
        from handlers.chat import handle_message_text
        await handle_message_text(update, context, transcript)

    except Exception as e:
        logger.exception("voice handling failed")
        await update.message.reply_text(f"Had trouble with that voice message: {e}")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos: download → save to vault → ask Claude to analyze."""
    user_id = update.effective_user.id
    if not auth_check(user_id):
        return

    photo = update.message.photo[-1] if update.message.photo else None  # Largest size
    if not photo:
        return

    caption = update.message.caption or ""
    logger.info(">>> User: [photo, %dx%d] %s", photo.width, photo.height, caption[:50])
    await update.message.chat.send_action("typing")

    try:
        # Download photo
        file = await context.bot.get_file(photo.file_id)
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        photo_dir = config.VAULT_PATH / "photos"
        photo_dir.mkdir(parents=True, exist_ok=True)
        photo_path = photo_dir / f"{date_str}.jpg"
        await file.download_to_drive(str(photo_path))

        logger.info("photo saved: %s", photo_path)

        # Ask Claude to analyze the image
        # claude --print with bypassPermissions can use Read tool on images
        message = f"User sent a photo (saved at {photo_path})."
        if caption:
            message += f' Caption: "{caption}"'
        message += " Please read and describe the image, then respond about it."

        # Pass to chat handler directly (Message is immutable)
        from handlers.chat import handle_message_text
        await handle_message_text(update, context, message)

    except Exception as e:
        logger.exception("photo handling failed")
        await update.message.reply_text(f"Had trouble with that photo: {e}")

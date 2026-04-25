"""Post-response hooks — gratitude + moments capture.

Keyword-based memory triggers replaced by dream consolidation (brain/dream.py).
Gratitude and moments capture remain as real-time hooks — they need immediate feedback.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from gratitude import add_gratitude, get_streak as get_gratitude_streak  # noqa: E402
from moments import add_moment, get_stats as get_moment_stats  # noqa: E402

logger = logging.getLogger(__name__)

GRATITUDE_TRIGGERS = [
    "grateful for", "thankful for", "appreciate that",
    "i'm grateful", "im grateful", "i am grateful",
    "gratitude:", "thankful:",
]

MOMENT_TRIGGERS = ["moment:", "memory:", "remember:"]
WIN_TRIGGERS = ["win:", "accomplishment:", "shipped:", "achieved:"]


def run_post_response_hooks(user_message: str, harry_response: str) -> None:
    msg_lower = user_message.lower()

    # Gratitude capture
    for trigger in GRATITUDE_TRIGGERS:
        if trigger in msg_lower:
            idx = msg_lower.find(trigger)
            gratitude_text = user_message[idx + len(trigger):].strip()
            if gratitude_text:
                try:
                    add_gratitude(gratitude_text)
                    streak = get_gratitude_streak()
                    logger.info("gratitude captured: %s (streak: %d)", gratitude_text[:50], streak)
                except Exception as e:
                    logger.warning("gratitude capture failed: %s", e)
            return  # Only capture one thing per message

    # Family moment capture
    for trigger in MOMENT_TRIGGERS:
        if trigger in msg_lower:
            idx = msg_lower.find(trigger)
            moment_text = user_message[idx + len(trigger):].strip()
            if moment_text:
                try:
                    entry = add_moment(moment_text, "family")
                    stats = get_moment_stats()
                    logger.info("moment captured: %s (total: %d)", moment_text[:50], stats["family"])
                except Exception as e:
                    logger.warning("moment capture failed: %s", e)
            return

    # Win capture
    for trigger in WIN_TRIGGERS:
        if trigger in msg_lower:
            idx = msg_lower.find(trigger)
            win_text = user_message[idx + len(trigger):].strip()
            if win_text:
                try:
                    entry = add_moment(win_text, "win")
                    stats = get_moment_stats()
                    logger.info("win captured: %s (total: %d)", win_text[:50], stats["wins"])
                except Exception as e:
                    logger.warning("win capture failed: %s", e)
            return

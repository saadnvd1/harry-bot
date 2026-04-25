"""Scheduled tasks runner — DB-backed cron tasks added/removed at runtime
without restart. Poll loop lives inside the worker daemon.

Schedule format: standard 5-field cron (m h dom mon dow) OR natural shortcuts
via `parse_schedule()` (e.g. "daily 9am", "every 30 minutes").
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta

from croniter import croniter
from zoneinfo import ZoneInfo

from config import Config
from worker import queue

logger = logging.getLogger(__name__)
config = Config()
TZ = ZoneInfo(config.TIMEZONE)

POLL_INTERVAL = 30  # check for due tasks every 30s


def parse_schedule(spec: str) -> str:
    """Turn a natural-ish schedule spec into a 5-field cron string.

    Accepted:
      - raw cron: "0 9 * * *"
      - "daily 9am", "daily at 9", "daily 14:30"
      - "hourly", "every hour"
      - "every 30 min", "every 2 hours"
      - "weekdays 9am"
      - "monday 9am" (or any weekday)
    """
    s = spec.strip().lower()

    # Raw 5-field cron
    if re.fullmatch(r"[\d\*/,\-]+(?:\s+[\d\*/,\-]+){4}", s):
        return s

    if s in {"hourly", "every hour"}:
        return "0 * * * *"
    if s in {"daily", "every day"}:
        return "0 9 * * *"

    m = re.fullmatch(r"every\s+(\d+)\s*(min|mins|minutes?|h|hour|hours?)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit.startswith("h"):
            return f"0 */{n} * * *"
        return f"*/{n} * * * *"

    # "daily 9am", "daily at 9:30", "daily 14:00"
    m = re.fullmatch(r"(?:daily|every\s+day)\s*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = m.group(3)
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
        return f"{minute} {hour} * * *"

    # "weekdays 9am"
    m = re.fullmatch(r"weekdays?\s*(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", s)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = m.group(3)
        if ap == "pm" and hour < 12:
            hour += 12
        return f"{minute} {hour} * * 1-5"

    # Single weekday
    days = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3,
            "thursday": 4, "friday": 5, "saturday": 6}
    for name, dow in days.items():
        m = re.fullmatch(rf"{name}s?\s*(?:at\s+)?(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)?", s)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            ap = m.group(3)
            if ap == "pm" and hour < 12:
                hour += 12
            return f"{minute} {hour} * * {dow}"

    raise ValueError(f"could not parse schedule: {spec!r}")


def next_fire_time(cron: str, from_ts: float | None = None) -> float:
    base = datetime.fromtimestamp(from_ts or time.time(), tz=TZ)
    it = croniter(cron, base)
    return it.get_next(datetime).timestamp()


def describe_cron(cron: str) -> str:
    """Pretty describe the next few runs for user feedback."""
    base = datetime.now(tz=TZ)
    it = croniter(cron, base)
    return ", ".join(
        it.get_next(datetime).strftime("%a %b %-d %-I:%M%p")
        for _ in range(3)
    )


async def scheduler_loop(shutdown: asyncio.Event):
    logger.info("scheduler started (poll=%ds, tz=%s)", POLL_INTERVAL, config.TIMEZONE)
    while not shutdown.is_set():
        try:
            _check_due()
        except Exception as e:
            logger.warning("scheduler tick error: %s", e)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
    logger.info("scheduler stopped")


def _check_due() -> None:
    now = time.time()
    due = queue.due_scheduled_tasks(now)
    for task in due:
        try:
            job_id = queue.enqueue(
                kind="raw",
                payload={
                    "user_id": task["user_id"],
                    "chat_id": task["chat_id"],
                    "prompt": task["prompt"],
                    "model": task.get("model"),
                    "scheduled_task_id": task["id"],
                    "scheduled_task_label": task.get("label"),
                },
                agent=task["agent"],
                stream_chat_id=task["chat_id"],
            )
            next_run = next_fire_time(task["cron"])
            queue.update_task_schedule(task["id"], next_run=next_run, last_run=now)
            logger.info(
                "scheduled task %d fired → job %d; next run %s",
                task["id"], job_id,
                datetime.fromtimestamp(next_run, tz=TZ).strftime("%a %-I:%M%p"),
            )
        except Exception as e:
            logger.exception("failed to fire scheduled task %d: %s", task["id"], e)
            # Advance next_run anyway so we don't thrash on a bad task
            try:
                queue.update_task_schedule(
                    task["id"],
                    next_run=next_fire_time(task["cron"]),
                    last_run=now,
                )
            except Exception:
                pass

"""Harry worker daemon. Polls SQLite queue, spawns one async task per job
(capped by WORKER_CONCURRENCY). Runs as a separate serviceman service from bot.py.

Start: `sm start harry-worker`
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Load .env before importing config
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from telegram import Bot  # noqa: E402

from config import Config  # noqa: E402
from worker import queue, runner, scheduler  # noqa: E402

LOG_FILE = Path(__file__).parent.parent / "harry.log"
logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3),
    ],
)
logger = logging.getLogger("worker")

POLL_FALLBACK = 60    # sec — paranoia poll, only fires if honker listener dies
CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "2"))
MAX_SLOTS = int(os.environ.get("WORKER_MAX_SLOTS", "2"))
STUCK_CHECK_INTERVAL = 300  # reset stuck jobs every 5 min

_shutdown = asyncio.Event()
_slot_lock_fh = None  # kept alive for process lifetime; OS releases on exit
_honker_db = None     # honker Database handle for WAL-based wake


def _acquire_slot() -> tuple[int, object] | tuple[None, None]:
    """Acquire an exclusive flock on one of MAX_SLOTS lock files.

    First-free-wins across harry-worker + harry-worker-2 (or however many
    serviceman services run). OS auto-releases the lock when the process dies
    (including SIGKILL), so orphan workers can't leak — the next startup
    reclaims their slot. Prevents the multi-generation-orphan zombie scenario.
    """
    data_dir = queue.DB_PATH.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    for slot in range(1, MAX_SLOTS + 1):
        path = data_dir / f"worker-{slot}.lock"
        # Open with O_CREAT|O_RDWR (no truncate) so a failed flock attempt
        # doesn't clobber the PID of the worker that currently holds the lock.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        fh = os.fdopen(fd, "r+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            continue
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        return slot, fh
    return None, None


def _handle_signal(signum, _frame):
    logger.warning("got signal %d, requesting shutdown", signum)
    try:
        asyncio.get_event_loop().call_soon_threadsafe(_shutdown.set)
    except RuntimeError:
        _shutdown.set()


async def _stuck_reaper():
    while not _shutdown.is_set():
        try:
            n = queue.reset_stuck_running(stuck_after_seconds=600)
            if n:
                logger.warning("reset %d stuck jobs back to pending", n)
        except Exception as e:
            logger.warning("stuck reaper error: %s", e)
        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=STUCK_CHECK_INTERVAL)
        except asyncio.TimeoutError:
            pass


def _init_honker():
    """Open honker Database for WAL-based job wake. Returns None if unavailable."""
    global _honker_db
    try:
        import honker
        _honker_db = honker.open(str(queue.DB_PATH))
        logger.info("honker: WAL-based wake enabled (listen on 'jobs' channel)")
        return _honker_db
    except ImportError:
        logger.warning("honker not installed — falling back to %ds poll", POLL_FALLBACK)
        return None
    except Exception as e:
        logger.warning("honker init failed (%s) — falling back to poll", e)
        return None


_job_wake: asyncio.Event | None = None  # set by honker listener background task


async def _honker_listener_task(hdb):
    """Background task: listen for WAL changes, set _job_wake on each."""
    try:
        async for _notification in hdb.listen("jobs"):
            if _job_wake:
                logger.debug("honker: WAL change detected, waking worker")
                _job_wake.set()
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning("honker listener died (%s) — workers fall back to poll", e)


async def _wait_for_wake():
    """Wait for honker WAL notification, paranoia poll, or shutdown."""
    if _job_wake:
        _job_wake.clear()
    # Wait for whichever fires first: job wake, shutdown, or timeout
    waiters = {asyncio.ensure_future(_shutdown.wait())}
    wake_fut = None
    if _job_wake:
        wake_fut = asyncio.ensure_future(_job_wake.wait())
        waiters.add(wake_fut)
    try:
        done, _ = await asyncio.wait(
            waiters,
            return_when=asyncio.FIRST_COMPLETED,
            timeout=POLL_FALLBACK,
        )
        shutdown_triggered = any(
            not f.cancelled() and f.done() and f is not wake_fut
            for f in done
        )
        # Log what woke us
        if wake_fut and wake_fut in done:
            logger.info("woke: honker WAL notify")
        elif not done:
            logger.debug("woke: poll fallback (%ds timeout)", POLL_FALLBACK)
        for f in waiters:
            if not f.done():
                f.cancel()
        return not shutdown_triggered
    except Exception:
        for f in waiters:
            if not f.done():
                f.cancel()
        return not _shutdown.is_set()


async def _run():
    config = Config()
    queue.init_db()

    bot = Bot(token=config.TELEGRAM_TOKEN)
    sem = asyncio.Semaphore(CONCURRENCY)
    in_flight: set[asyncio.Task] = set()

    # Init honker WAL watcher — replaces sleep-polling
    global _job_wake
    _job_wake = asyncio.Event()
    hdb = _init_honker()
    honker_task = None
    if hdb:
        honker_task = asyncio.create_task(
            _honker_listener_task(hdb), name="honker-listener"
        )

    logger.info("worker started (concurrency=%d, db=%s)", CONCURRENCY, queue.DB_PATH)

    reaper = asyncio.create_task(_stuck_reaper(), name="stuck-reaper")
    sched = asyncio.create_task(scheduler.scheduler_loop(_shutdown), name="scheduler")

    async def _handle(job):
        async with sem:
            try:
                await runner.run_job(bot, job)
            except Exception:
                logger.exception("runner crashed on job %d", job.id)
                try:
                    queue.mark_error(job.id, "runner crash")
                except Exception:
                    pass

    while not _shutdown.is_set():
        # Clean up finished tasks
        in_flight = {t for t in in_flight if not t.done()}

        if len(in_flight) >= CONCURRENCY:
            # At capacity — brief sleep then recheck
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass
            continue

        job = None
        try:
            job = queue.claim_next()
        except Exception as e:
            logger.error("claim_next failed: %s", e)
            await asyncio.sleep(2)
            continue

        if job is None:
            # No job — wait for honker WAL wake or paranoia poll
            await _wait_for_wake()
            continue

        task = asyncio.create_task(_handle(job), name=f"job-{job.id}")
        in_flight.add(task)

    # Shutdown: wait for in-flight to finish (bounded)
    logger.info("shutdown requested, draining %d in-flight jobs…", len(in_flight))
    if honker_task:
        honker_task.cancel()
    reaper.cancel()
    sched.cancel()
    if in_flight:
        done, pending = await asyncio.wait(in_flight, timeout=30)
        for t in pending:
            t.cancel()
    logger.info("worker stopped")


def main():
    global _slot_lock_fh
    queue.init_db()  # ensure data dir exists before slot acquisition

    # Retry briefly — graceful shutdown of a prior instance may still be
    # releasing its lock when we start up during a restart.
    import time
    slot = None
    for attempt in range(10):
        slot, _slot_lock_fh = _acquire_slot()
        if slot is not None:
            break
        time.sleep(1)

    if slot is None:
        logger.error(
            "no free worker slot (tried 1..%d, 10 retries) — another worker "
            "holds all locks. Refusing to start to prevent orphan buildup.",
            MAX_SLOTS,
        )
        sys.exit(0)
    logger.info("acquired worker slot %d", slot)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        loop.run_until_complete(_run())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()

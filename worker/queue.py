"""SQLite-backed job queue. Shared between bot (enqueuer) and worker (consumer).

WAL mode for concurrent read/write. Single writer at a time via BEGIN IMMEDIATE
for the dequeue claim step.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "data" / "queue.db"

# --- Honker (WAL-based notify/listen, replaces polling) ---------------------
_honker_db = None


def _get_honker():
    """Lazy-init honker Database handle (shared across enqueue calls)."""
    global _honker_db
    if _honker_db is None:
        try:
            import honker
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _honker_db = honker.open(str(DB_PATH))
        except ImportError:
            pass  # honker not installed — fall back to polling
    return _honker_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    agent TEXT NOT NULL DEFAULT 'claude',
    stream_chat_id INTEGER,
    stream_msg_id INTEGER,
    result TEXT,
    error TEXT,
    retries INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    done_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS sessions (
    user_id INTEGER NOT NULL,
    agent TEXT NOT NULL,
    session_id TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, agent)
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    cron TEXT NOT NULL,
    prompt TEXT NOT NULL,
    agent TEXT NOT NULL DEFAULT 'claude',
    model TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run REAL NOT NULL,
    last_run REAL,
    label TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_next_run ON scheduled_tasks(enabled, next_run);

CREATE TABLE IF NOT EXISTS user_state (
    user_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);
"""


MIGRATIONS = [
    # (table, column, type-with-default). Applied only if column missing.
    ("jobs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
    ("sessions", "turn_count", "INTEGER NOT NULL DEFAULT 0"),
    ("jobs", "private", "INTEGER NOT NULL DEFAULT 0"),
]


@dataclass
class Job:
    id: int
    kind: str
    payload: dict
    status: str
    agent: str
    stream_chat_id: int | None
    stream_msg_id: int | None
    retries: int


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if col not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def enqueue(
    kind: str,
    payload: dict,
    agent: str = "claude",
    stream_chat_id: int | None = None,
) -> int:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (kind, payload, agent, stream_chat_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, json.dumps(payload), agent, stream_chat_id, time.time()),
        )
        job_id = cur.lastrowid
    # Notify workers via honker (WAL-based wake, ~1ms delivery)
    hdb = _get_honker()
    if hdb:
        try:
            with hdb.transaction() as tx:
                tx.notify("jobs", {"id": job_id})
        except Exception:
            pass  # notify is best-effort; workers have paranoia poll fallback
    return job_id


def claim_next() -> Job | None:
    """Atomically claim the next pending job (skipping cancel-requested ones)."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='pending' AND cancel_requested=0 "
            "ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            conn.execute("COMMIT")
            return Job(
                id=row["id"],
                kind=row["kind"],
                payload=json.loads(row["payload"]),
                status="running",
                agent=row["agent"],
                stream_chat_id=row["stream_chat_id"],
                stream_msg_id=row["stream_msg_id"],
                retries=row["retries"],
            )
        # Also sweep pending+cancel_requested → cancelled (never ran)
        conn.execute(
            "UPDATE jobs SET status='cancelled', done_at=? "
            "WHERE status='pending' AND cancel_requested=1",
            (time.time(),),
        )
        conn.execute("COMMIT")
        return None


def set_stream_msg_id(job_id: int, msg_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET stream_msg_id=? WHERE id=?", (msg_id, job_id)
        )


def mark_done(job_id: int, result: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='done', result=?, done_at=? WHERE id=?",
            (result, time.time(), job_id),
        )


def mark_error(job_id: int, error: str, retry: bool = False) -> None:
    """Mark error. If retry=True, bump retry count and requeue as pending."""
    with _connect() as conn:
        row = conn.execute("SELECT retries FROM jobs WHERE id=?", (job_id,)).fetchone()
        retries = (row["retries"] if row else 0) + 1
        if retry and retries < 3:
            conn.execute(
                "UPDATE jobs SET status='pending', retries=?, error=? WHERE id=?",
                (retries, error, job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET status='error', retries=?, error=?, done_at=? WHERE id=?",
                (retries, error, time.time(), job_id),
            )


def reset_stuck_running(stuck_after_seconds: int = 600) -> int:
    """Worker crash recovery: jobs stuck in 'running' for too long get requeued."""
    cutoff = time.time() - stuck_after_seconds
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status='pending' WHERE status='running' AND started_at < ?",
            (cutoff,),
        )
        return cur.rowcount


def recent_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, kind, status, agent, created_at, done_at, error "
            "FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_session_id(user_id: int, agent: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_id FROM sessions WHERE user_id=? AND agent=?",
            (user_id, agent),
        ).fetchone()
        return row["session_id"] if row else None


def is_session_in_use(user_id: int, agent: str) -> bool:
    """Check if this user+agent has a running job (session actively in use).

    Used to prevent concurrent --resume to the same Claude session, which
    causes the second job to inherit context from the first job's conversation.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs "
            "WHERE json_extract(payload, '$.user_id')=? "
            "  AND agent=? "
            "  AND status='running' "
            "LIMIT 1",
            (user_id, agent),
        ).fetchone()
        return row is not None


def set_session_id(user_id: int, agent: str, session_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (user_id, agent, session_id, updated_at, turn_count) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(user_id, agent) DO UPDATE SET "
            "session_id=excluded.session_id, updated_at=excluded.updated_at, "
            "turn_count=CASE WHEN sessions.session_id=excluded.session_id "
            "THEN sessions.turn_count+1 ELSE 1 END",
            (user_id, agent, session_id, time.time()),
        )


# Max turns before session auto-reset. Session history grows with each turn,
# consuming tokens on every subsequent message. Reset keeps context fresh.
MAX_SESSION_TURNS = 20


def should_reset_session(user_id: int, agent: str) -> bool:
    """Check if session has exceeded max turns and should be reset."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT turn_count, updated_at FROM sessions WHERE user_id=? AND agent=?",
            (user_id, agent),
        ).fetchone()
        if not row:
            return False
        # Reset if too many turns OR session is older than 4 hours
        turn_limit = row["turn_count"] >= MAX_SESSION_TURNS
        age_limit = (time.time() - row["updated_at"]) > 4 * 3600
        return turn_limit or age_limit


def reset_session(user_id: int, agent: str) -> None:
    """Clear session to start fresh (saves tokens on --resume)."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE user_id=? AND agent=?",
            (user_id, agent),
        )


def clear_session(user_id: int, agent: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE user_id=? AND agent=?", (user_id, agent)
        )


# --- Cancellation (steer queue mode) -----------------------------------------

def request_cancel_for_user(
    user_id: int, kinds: tuple[str, ...] = ("chat",),
    include_protected: bool = False,
) -> list[int]:
    """Mark cancel_requested on pending/running jobs of given kinds for this user.
    Returns the job IDs that got marked. Actual cancellation happens in the worker.

    Protected jobs (payload.protected=1, set by !r research prefix) are skipped
    by default so rapid follow-up messages don't kill long-running research.
    Pass include_protected=True for explicit cancel commands ("stop", "cancel").
    """
    placeholders = ",".join("?" * len(kinds))
    protected_clause = "" if include_protected else (
        "  AND IFNULL(json_extract(payload, '$.protected'), 0) != 1 "
    )
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT id FROM jobs "
            f"WHERE json_extract(payload, '$.user_id')=? "
            f"  AND kind IN ({placeholders}) "
            f"  AND status IN ('pending','running') "
            f"  AND cancel_requested=0"
            f"{protected_clause}",
            (user_id, *kinds),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            conn.executemany(
                "UPDATE jobs SET cancel_requested=1 WHERE id=?",
                [(i,) for i in ids],
            )
        return ids


def is_cancel_requested(job_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        return bool(row and row["cancel_requested"])


def mark_cancelled(job_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='cancelled', done_at=? WHERE id=?",
            (time.time(), job_id),
        )


# --- Scheduled tasks ---------------------------------------------------------

def add_scheduled_task(
    user_id: int,
    chat_id: int,
    cron: str,
    prompt: str,
    next_run: float,
    agent: str = "claude",
    model: str | None = None,
    label: str | None = None,
) -> int:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_tasks "
            "(user_id, chat_id, cron, prompt, agent, model, next_run, label, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, cron, prompt, agent, model, next_run, label, time.time()),
        )
        return cur.lastrowid


def list_scheduled_tasks(user_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE user_id=? ORDER BY id", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_scheduled_task(task_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_scheduled_task(task_id: int, user_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM scheduled_tasks WHERE id=? AND user_id=?", (task_id, user_id)
        )
        return cur.rowcount > 0


def due_scheduled_tasks(now: float) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run<=? ORDER BY next_run",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_task_schedule(task_id: int, next_run: float, last_run: float) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE scheduled_tasks SET next_run=?, last_run=? WHERE id=?",
            (next_run, last_run, task_id),
        )


# --- User state (per-user persistent flags) ----------------------------------

def get_user_state_with_ts(user_id: int, key: str) -> tuple[str, float] | None:
    """Return (value, updated_at) for expiration checks. None if not set."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value, updated_at FROM user_state WHERE user_id=? AND key=?",
            (user_id, key),
        ).fetchone()
        return (row["value"], row["updated_at"]) if row else None


def get_user_state(user_id: int, key: str, default: str | None = None) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM user_state WHERE user_id=? AND key=?",
            (user_id, key),
        ).fetchone()
        return row["value"] if row else default


def set_user_state(user_id: int, key: str, value: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_state (user_id, key, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (user_id, key, value, time.time()),
        )


def delete_user_state(user_id: int, key: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM user_state WHERE user_id=? AND key=?",
            (user_id, key),
        )

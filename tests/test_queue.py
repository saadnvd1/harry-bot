"""Tests for worker/queue.py — SQLite job queue operations."""

import os
import tempfile
import time
import pytest

# Patch DB_PATH before importing queue module
import worker.queue as queue_module


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_path = f.name

    # Patch the DB_PATH
    from pathlib import Path
    original_path = queue_module.DB_PATH
    queue_module.DB_PATH = Path(temp_path)

    # Initialize the DB
    queue_module.init_db()

    yield temp_path

    # Cleanup
    queue_module.DB_PATH = original_path
    os.unlink(temp_path)
    # Also clean up WAL files if they exist
    for suffix in ["-wal", "-shm"]:
        try:
            os.unlink(temp_path + suffix)
        except FileNotFoundError:
            pass


class TestEnqueueAndClaim:
    """Test basic job enqueue and claim operations."""

    def test_enqueue_returns_job_id(self, temp_db):
        job_id = queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123, "text": "hello"},
            agent="claude",
        )
        assert isinstance(job_id, int)
        assert job_id > 0

    def test_claim_next_returns_job(self, temp_db):
        queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123, "text": "hello"},
            agent="claude",
        )

        job = queue_module.claim_next()
        assert job is not None
        assert job.kind == "chat"
        assert job.payload["user_id"] == 123
        assert job.status == "running"

    def test_claim_empty_queue_returns_none(self, temp_db):
        job = queue_module.claim_next()
        assert job is None

    def test_claimed_job_not_reclaimed(self, temp_db):
        queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )

        job1 = queue_module.claim_next()
        job2 = queue_module.claim_next()

        assert job1 is not None
        assert job2 is None

    def test_fifo_order(self, temp_db):
        queue_module.enqueue(kind="chat", payload={"order": 1}, agent="claude")
        queue_module.enqueue(kind="chat", payload={"order": 2}, agent="claude")
        queue_module.enqueue(kind="chat", payload={"order": 3}, agent="claude")

        job1 = queue_module.claim_next()
        job2 = queue_module.claim_next()
        job3 = queue_module.claim_next()

        assert job1.payload["order"] == 1
        assert job2.payload["order"] == 2
        assert job3.payload["order"] == 3


class TestJobCompletion:
    """Test job completion marking."""

    def test_mark_done(self, temp_db):
        job_id = queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )
        queue_module.claim_next()
        queue_module.mark_done(job_id, "response text")

        jobs = queue_module.recent_jobs(limit=1)
        assert jobs[0]["status"] == "done"

    def test_mark_error_no_retry(self, temp_db):
        job_id = queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )
        queue_module.claim_next()
        queue_module.mark_error(job_id, "something went wrong", retry=False)

        jobs = queue_module.recent_jobs(limit=1)
        assert jobs[0]["status"] == "error"
        assert jobs[0]["error"] == "something went wrong"

    def test_mark_error_with_retry(self, temp_db):
        job_id = queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )
        queue_module.claim_next()
        queue_module.mark_error(job_id, "transient error", retry=True)

        jobs = queue_module.recent_jobs(limit=1)
        assert jobs[0]["status"] == "pending"  # Back in queue


class TestCancellation:
    """Test job cancellation (steer queue mode)."""

    def test_request_cancel_marks_jobs(self, temp_db):
        queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )

        cancelled = queue_module.request_cancel_for_user(123, kinds=("chat",))
        assert len(cancelled) == 1

    def test_cancelled_jobs_not_claimed(self, temp_db):
        queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )
        queue_module.request_cancel_for_user(123, kinds=("chat",))

        job = queue_module.claim_next()
        assert job is None

    def test_is_cancel_requested(self, temp_db):
        job_id = queue_module.enqueue(
            kind="chat",
            payload={"user_id": 123},
            agent="claude",
        )

        assert not queue_module.is_cancel_requested(job_id)
        queue_module.request_cancel_for_user(123, kinds=("chat",))
        assert queue_module.is_cancel_requested(job_id)


class TestSessions:
    """Test session management."""

    def test_get_session_id_none_initially(self, temp_db):
        session_id = queue_module.get_session_id(123, "claude")
        assert session_id is None

    def test_set_and_get_session_id(self, temp_db):
        queue_module.set_session_id(123, "claude", "sess-abc-123")
        session_id = queue_module.get_session_id(123, "claude")
        assert session_id == "sess-abc-123"

    def test_different_agents_different_sessions(self, temp_db):
        queue_module.set_session_id(123, "claude", "claude-session")
        queue_module.set_session_id(123, "ollama", "ollama-session")

        assert queue_module.get_session_id(123, "claude") == "claude-session"
        assert queue_module.get_session_id(123, "ollama") == "ollama-session"

    def test_clear_session(self, temp_db):
        queue_module.set_session_id(123, "claude", "session-to-clear")
        queue_module.clear_session(123, "claude")

        assert queue_module.get_session_id(123, "claude") is None


class TestScheduledTasks:
    """Test scheduled task operations."""

    def test_add_scheduled_task(self, temp_db):
        task_id = queue_module.add_scheduled_task(
            user_id=123,
            chat_id=456,
            cron="0 9 * * *",
            prompt="morning briefing",
            next_run=time.time() + 3600,
            agent="claude",
            label="morning",
        )
        assert isinstance(task_id, int)
        assert task_id > 0

    def test_list_scheduled_tasks(self, temp_db):
        queue_module.add_scheduled_task(
            user_id=123,
            chat_id=456,
            cron="0 9 * * *",
            prompt="task 1",
            next_run=time.time(),
        )
        queue_module.add_scheduled_task(
            user_id=123,
            chat_id=456,
            cron="0 18 * * *",
            prompt="task 2",
            next_run=time.time(),
        )

        tasks = queue_module.list_scheduled_tasks(123)
        assert len(tasks) == 2

    def test_delete_scheduled_task(self, temp_db):
        task_id = queue_module.add_scheduled_task(
            user_id=123,
            chat_id=456,
            cron="0 9 * * *",
            prompt="to delete",
            next_run=time.time(),
        )

        deleted = queue_module.delete_scheduled_task(task_id, 123)
        assert deleted is True

        tasks = queue_module.list_scheduled_tasks(123)
        assert len(tasks) == 0

    def test_due_scheduled_tasks(self, temp_db):
        past_time = time.time() - 100
        future_time = time.time() + 3600

        queue_module.add_scheduled_task(
            user_id=123,
            chat_id=456,
            cron="* * * * *",
            prompt="due task",
            next_run=past_time,
        )
        queue_module.add_scheduled_task(
            user_id=123,
            chat_id=456,
            cron="* * * * *",
            prompt="future task",
            next_run=future_time,
        )

        due = queue_module.due_scheduled_tasks(time.time())
        assert len(due) == 1
        assert due[0]["prompt"] == "due task"

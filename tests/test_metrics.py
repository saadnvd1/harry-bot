"""Tests for brain/metrics.py — structured logging and metrics."""

import os
import tempfile
import time
import pytest
from pathlib import Path

import brain.metrics as metrics_module
from brain.metrics import RequestMetrics, log_request, log_structured, recent_metrics, summarize_metrics


@pytest.fixture
def temp_metrics_log():
    """Create a temporary metrics log file."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        temp_path = Path(f.name)

    original_path = metrics_module.METRICS_LOG
    metrics_module.METRICS_LOG = temp_path

    yield temp_path

    metrics_module.METRICS_LOG = original_path
    if temp_path.exists():
        os.unlink(temp_path)


class TestRequestMetrics:
    """Test RequestMetrics dataclass."""

    def test_create_metrics(self):
        m = RequestMetrics(
            timestamp=time.time(),
            job_id=1,
            kind="chat",
            agent="claude",
            model="claude-sonnet-4-6",
            tier="normal",
            user_id=123,
            response_time_ms=1500,
            response_chars=500,
            tool_calls=2,
            stop_reason="end_turn",
            status="done",
        )
        assert m.job_id == 1
        assert m.agent == "claude"
        assert m.status == "done"

    def test_optional_fields(self):
        m = RequestMetrics(
            timestamp=time.time(),
            job_id=1,
            kind="chat",
            agent="ollama",
            model=None,
            tier=None,
            user_id=None,
            response_time_ms=100,
            response_chars=50,
            tool_calls=0,
            stop_reason=None,
            status="done",
        )
        assert m.model is None
        assert m.error is None


class TestLogRequest:
    """Test logging metrics to file."""

    def test_log_request_creates_file(self, temp_metrics_log):
        m = RequestMetrics(
            timestamp=time.time(),
            job_id=1,
            kind="chat",
            agent="claude",
            model=None,
            tier="normal",
            user_id=123,
            response_time_ms=1000,
            response_chars=100,
            tool_calls=0,
            stop_reason="end_turn",
            status="done",
        )
        log_request(m)

        assert temp_metrics_log.exists()
        content = temp_metrics_log.read_text()
        assert "claude" in content
        assert '"job_id": 1' in content

    def test_multiple_logs_appended(self, temp_metrics_log):
        for i in range(3):
            m = RequestMetrics(
                timestamp=time.time(),
                job_id=i,
                kind="chat",
                agent="claude",
                model=None,
                tier="normal",
                user_id=123,
                response_time_ms=1000,
                response_chars=100,
                tool_calls=0,
                stop_reason="end_turn",
                status="done",
            )
            log_request(m)

        lines = temp_metrics_log.read_text().strip().split("\n")
        assert len(lines) == 3


class TestLogStructured:
    """Test the convenience log_structured function."""

    def test_log_structured_calculates_response_time(self, temp_metrics_log):
        start = time.time() - 1.5  # 1.5 seconds ago
        log_structured(
            job_id=1,
            kind="chat",
            agent="claude",
            model=None,
            tier="normal",
            user_id=123,
            start_time=start,
            status="done",
            response_chars=100,
        )

        metrics = recent_metrics(limit=1)
        assert len(metrics) == 1
        # Should be approximately 1500ms
        assert 1400 < metrics[0]["response_time_ms"] < 1600


class TestRecentMetrics:
    """Test reading recent metrics."""

    def test_recent_metrics_empty(self, temp_metrics_log):
        metrics = recent_metrics()
        assert metrics == []

    def test_recent_metrics_returns_list(self, temp_metrics_log):
        log_structured(
            job_id=1,
            kind="chat",
            agent="claude",
            model=None,
            tier="normal",
            user_id=123,
            start_time=time.time(),
            status="done",
        )

        metrics = recent_metrics()
        assert len(metrics) == 1
        assert metrics[0]["job_id"] == 1

    def test_recent_metrics_respects_limit(self, temp_metrics_log):
        for i in range(10):
            log_structured(
                job_id=i,
                kind="chat",
                agent="claude",
                model=None,
                tier="normal",
                user_id=123,
                start_time=time.time(),
                status="done",
            )

        metrics = recent_metrics(limit=5)
        assert len(metrics) == 5
        # Should get the last 5
        assert metrics[-1]["job_id"] == 9


class TestSummarizeMetrics:
    """Test metrics summarization."""

    def test_summarize_empty(self, temp_metrics_log):
        summary = summarize_metrics(hours=24)
        assert summary["count"] == 0

    def test_summarize_counts_by_agent(self, temp_metrics_log):
        for agent in ["claude", "claude", "ollama"]:
            log_structured(
                job_id=1,
                kind="chat",
                agent=agent,
                model=None,
                tier="normal",
                user_id=123,
                start_time=time.time(),
                status="done",
            )

        summary = summarize_metrics(hours=24)
        assert summary["count"] == 3
        assert summary["by_agent"]["claude"] == 2
        assert summary["by_agent"]["ollama"] == 1

    def test_summarize_counts_by_status(self, temp_metrics_log):
        for status in ["done", "done", "error"]:
            log_structured(
                job_id=1,
                kind="chat",
                agent="claude",
                model=None,
                tier="normal",
                user_id=123,
                start_time=time.time(),
                status=status,
            )

        summary = summarize_metrics(hours=24)
        assert summary["by_status"]["done"] == 2
        assert summary["by_status"]["error"] == 1
        assert abs(summary["error_rate"] - 1/3) < 0.01

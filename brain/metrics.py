"""Structured logging and metrics for observability.

Logs key metrics as JSON for easy parsing/aggregation. Zero external dependencies.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# JSON log file for structured metrics (separate from main log)
METRICS_LOG = Path(__file__).parent.parent / "data" / "metrics.jsonl"


@dataclass
class RequestMetrics:
    """Metrics for a single request/job."""
    timestamp: float
    job_id: int
    kind: str  # chat, briefing, checkin, reflection, raw
    agent: str  # claude, ollama, codex, opencode
    model: Optional[str]
    tier: Optional[str]  # none, light, normal, full (enrichment tier)
    user_id: Optional[int]

    # Performance
    response_time_ms: int

    # Output stats
    response_chars: int
    tool_calls: int
    stop_reason: Optional[str]

    # Status
    status: str  # done, error, cancelled
    error: Optional[str] = None

    # Context (rough estimates)
    input_chars: int = 0  # prompt length
    fallback_used: bool = False

    # Cost tracking
    complexity: Optional[str] = None  # simple/medium/complex
    estimated_cost_usd: Optional[float] = None
    actual_cost_usd: Optional[float] = None


def log_request(metrics: RequestMetrics) -> None:
    """Log request metrics as JSON line."""
    try:
        METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(METRICS_LOG, "a") as f:
            f.write(json.dumps(asdict(metrics)) + "\n")
    except Exception as e:
        logger.warning("failed to write metrics: %s", e)


def log_structured(
    job_id: int,
    kind: str,
    agent: str,
    model: Optional[str],
    tier: Optional[str],
    user_id: Optional[int],
    start_time: float,
    status: str,
    response_chars: int = 0,
    tool_calls: int = 0,
    stop_reason: Optional[str] = None,
    error: Optional[str] = None,
    input_chars: int = 0,
    fallback_used: bool = False,
    complexity: Optional[str] = None,
    estimated_cost_usd: Optional[float] = None,
    actual_cost_usd: Optional[float] = None,
) -> None:
    """Convenience wrapper for logging request metrics."""
    metrics = RequestMetrics(
        timestamp=time.time(),
        job_id=job_id,
        kind=kind,
        agent=agent,
        model=model,
        tier=tier,
        user_id=user_id,
        response_time_ms=int((time.time() - start_time) * 1000),
        response_chars=response_chars,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        status=status,
        error=error,
        input_chars=input_chars,
        fallback_used=fallback_used,
        complexity=complexity,
        estimated_cost_usd=estimated_cost_usd,
        actual_cost_usd=actual_cost_usd,
    )
    log_request(metrics)

    cost_str = ""
    if actual_cost_usd is not None:
        cost_str = f" cost=${actual_cost_usd:.4f}"
    elif estimated_cost_usd is not None:
        cost_str = f" est_cost=${estimated_cost_usd:.4f}"

    logger.info(
        "METRIC job=%d agent=%s model=%s complexity=%s tier=%s status=%s "
        "time_ms=%d chars=%d tools=%d%s",
        job_id, agent, model or "default", complexity or "?",
        tier or "unknown", status, metrics.response_time_ms,
        response_chars, tool_calls, cost_str,
    )


def recent_metrics(limit: int = 100) -> list[dict]:
    """Read recent metrics from the log file."""
    if not METRICS_LOG.exists():
        return []

    lines = METRICS_LOG.read_text().strip().split("\n")
    results = []
    for line in lines[-limit:]:
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return results


def summarize_metrics(hours: float = 24) -> dict:
    """Generate summary statistics for the last N hours."""
    metrics = recent_metrics(limit=10000)
    cutoff = time.time() - (hours * 3600)

    recent = [m for m in metrics if m.get("timestamp", 0) > cutoff]

    if not recent:
        return {"period_hours": hours, "count": 0}

    # Aggregate stats
    by_agent = {}
    by_status = {}
    total_time = 0
    total_chars = 0
    total_tools = 0

    for m in recent:
        agent = m.get("agent", "unknown")
        status = m.get("status", "unknown")

        by_agent[agent] = by_agent.get(agent, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        total_time += m.get("response_time_ms", 0)
        total_chars += m.get("response_chars", 0)
        total_tools += m.get("tool_calls", 0)

    count = len(recent)
    return {
        "period_hours": hours,
        "count": count,
        "by_agent": by_agent,
        "by_status": by_status,
        "avg_response_time_ms": total_time // count if count else 0,
        "total_chars": total_chars,
        "total_tools": total_tools,
        "error_rate": by_status.get("error", 0) / count if count else 0,
    }

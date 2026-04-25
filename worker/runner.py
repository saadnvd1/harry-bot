"""Job runner — claims one job, invokes the right adapter, streams to channel,
records result. Runs inside the worker daemon.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from telegram import Bot

from config import Config
from agents import get_adapter
from agents.base import AgentEvent
from agents.fallback import (
    is_rate_limited, is_context_overflow, is_transient, get_fallback,
    should_escalate, get_escalation,
)
from brain.costs import estimate_cost, build_cost_report
# Import all adapters so they register themselves with the adapter registry
# Import all adapters so they register themselves with the adapter registry
import agents.claude   # noqa: F401
import agents.ollama   # noqa: F401
import agents.codex    # noqa: F401
import agents.opencode  # noqa: F401
import agents.gemini   # noqa: F401
from brain import history
from brain.prompts import get_system_prompt
from channels.telegram import TelegramRenderer
from worker import queue
from worker.enrich import (
    enrich_briefing_prompt,
    enrich_checkin_prompt,
    enrich_chat_prompt,
    get_briefing_prefix,
)
from worker.hooks import run_post_response_hooks
from brain.metrics import log_structured

logger = logging.getLogger(__name__)


def _log_tool_calls(job: queue.Job, tool_lines: list[str]) -> None:
    """Append tool calls to daily JSONL log for observability."""
    from datetime import datetime
    from pathlib import Path
    log_dir = Path(Config().VAULT_PATH) / "tool-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    import json
    entry = {
        "ts": datetime.now().isoformat(),
        "job_id": job.id,
        "kind": job.kind,
        "agent": job.agent,
        "tools": tool_lines,
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("tool log write failed: %s", e)


async def run_job(bot: Bot, job: queue.Job) -> None:
    """Handle one claimed job end-to-end."""
    logger.info("job %d start: kind=%s agent=%s", job.id, job.kind, job.agent)
    t0 = time.time()

    chat_id = job.stream_chat_id or job.payload.get("chat_id")
    if not chat_id:
        queue.mark_error(job.id, "no chat_id on job")
        return

    renderer = TelegramRenderer(bot, chat_id)
    try:
        await renderer.start()
        queue.set_stream_msg_id(job.id, renderer.msg_id)
    except Exception as e:
        logger.exception("failed to start telegram message")
        queue.mark_error(job.id, f"telegram start: {e}", retry=True)
        return

    try:
        prompt, user_message, user_id, model = _build_prompt(job)
        is_private = job.payload.get("private", False)

        # Append user message to history BEFORE calling agent (skip for private sessions)
        if job.kind == "chat" and user_message and user_id and not is_private:
            history.append(user_id, "user", user_message)

        prior_session = None
        # Only resume sessions for chat jobs — proactive jobs (briefing,
        # checkin, reflection) should start fresh to avoid stale context
        if user_id and job.kind == "chat":
            if queue.should_reset_session(user_id, job.agent):
                logger.info("job %d: session auto-reset (turn limit or age)", job.id)
                queue.reset_session(user_id, job.agent)
            elif queue.is_session_in_use(user_id, job.agent):
                # Another job is already using this session — skip resume to
                # avoid concurrent access to the same Claude conversation
                logger.info("job %d: session in use by another job, starting fresh", job.id)
            else:
                prior_session = queue.get_session_id(user_id, job.agent)

        # For briefings, prepend the Python-built schedule/weather/reminders
        # before Claude's analysis. Guarantees correct times.
        briefing_prefix = None
        if job.kind == "briefing":
            try:
                briefing_prefix = get_briefing_prefix()
            except Exception as e:
                logger.warning("briefing prefix failed: %s", e)

        complexity = job.payload.get("complexity", "medium")

        # Check for active persona (chat jobs only)
        active_persona = None
        if user_id and job.kind == "chat":
            active_persona = _check_persona(user_id)
            # Persona model override (unless user explicitly set one via prefix)
            if active_persona and not model:
                from brain.personas import get_persona
                persona = get_persona(active_persona)
                if persona and persona.model:
                    model = persona.model

        raw_events = _invoke_with_fallback(
            agent=job.agent,
            prompt=prompt,
            system_prompt=get_system_prompt(persona_name=active_persona),
            session_id=prior_session,
            model=model,
            timeout=job.payload.get("timeout", 1200 if complexity == "complex" else 600),
            renderer=renderer,
            complexity=complexity,
        )
        events = _cancel_guard(job.id, raw_events)
        if briefing_prefix:
            events = _prepend_text(briefing_prefix, events)
        summary = await renderer.consume(events)

        # Persist session id for resume
        if user_id and summary.get("session_id"):
            queue.set_session_id(user_id, job.agent, summary["session_id"])

        if summary.get("cancelled"):
            # Save partial output so the next turn sees what was tried — otherwise
            # a fresh Claude spawn confabulates to fill the gap. Tagged as
            # interrupted so dream consolidation won't extract "memories" from it.
            if job.kind == "chat" and user_message and user_id and not is_private:
                partial_text = (summary.get("text") or "").strip()
                tool_lines = summary.get("tool_lines", [])
                if partial_text or tool_lines:
                    body = "\n".join(tool_lines)
                    if partial_text:
                        body = f"{body}\n\n{partial_text}" if body else partial_text
                    history.append(
                        user_id, "assistant",
                        f"[INTERRUPTED — partial output, user steered before completion]\n{body}",
                        interrupted=True,
                    )
            queue.mark_cancelled(job.id)
        elif summary.get("error"):
            queue.mark_error(job.id, summary["error"])
        else:
            response = (summary.get("text") or "").strip()
            queue.mark_done(job.id, response)
            # History + hooks only for chat-kind jobs (skip both for private sessions)
            if job.kind == "chat" and user_message and user_id and not is_private:
                # Include tool calls in history so context is complete
                tool_lines = summary.get("tool_lines", [])
                if tool_lines:
                    full_response = "\n".join(tool_lines) + "\n\n" + response
                else:
                    full_response = response
                history.append(user_id, "assistant", full_response)
                run_post_response_hooks(user_message, response)

        # Log tool calls to separate daily file (all job kinds)
        tool_lines = summary.get("tool_lines", [])
        if tool_lines:
            _log_tool_calls(job, tool_lines)

        status = "cancelled" if summary.get("cancelled") else (
            "error" if summary.get("error") else "done"
        )
        response_chars = len(summary.get("text") or "")
        tool_count = summary.get("tool_count", 0)

        # Build cost report from usage data
        usage = summary.get("usage") or {}
        final_model = summary.get("final_model", model)
        cost_report = build_cost_report(
            model=final_model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            actual_cost_usd=usage.get("cost_usd"),
        )

        # Log structured metrics
        log_structured(
            job_id=job.id,
            kind=job.kind,
            agent=summary.get("final_agent", job.agent),
            model=final_model,
            tier=job.payload.get("tier"),
            user_id=user_id,
            start_time=t0,
            status=status,
            response_chars=response_chars,
            tool_calls=tool_count,
            stop_reason=summary.get("stop_reason"),
            error=summary.get("error"),
            input_chars=len(prompt),
            fallback_used=summary.get("fallback_used", False),
            complexity=complexity,
            estimated_cost_usd=cost_report.estimated_cost_usd,
            actual_cost_usd=cost_report.actual_cost_usd,
        )

        logger.info(
            "job %d %s in %.1fs (tools=%d, chars=%d, stop=%s)",
            job.id, status,
            time.time() - t0,
            tool_count,
            response_chars,
            summary.get("stop_reason"),
        )

    except Exception as e:
        logger.exception("job %d failed", job.id)
        try:
            await renderer.mark_failed(f"{type(e).__name__}: {e}")
        except Exception:
            pass
        queue.mark_error(job.id, f"{type(e).__name__}: {e}")


async def _invoke_with_fallback(
    agent: str,
    prompt: str,
    system_prompt: str,
    session_id: str | None,
    model: str | None,
    timeout: int,
    renderer: TelegramRenderer,
    complexity: str = "medium",
):
    """Invoke adapter with automatic fallback on rate limit + quality escalation.

    Two retry mechanisms:
      1. Rate-limit fallback — switch to different provider (codex, ollama)
      2. Quality escalation — response too weak for complexity → stronger model

    Single escalation attempt max to prevent runaway costs.
    """
    tried = set()
    current_agent = agent
    current_model = model
    current_session = session_id
    dropped_session = False
    already_escalated = False
    transient_retried = False

    while True:
        tried.add(current_agent)
        adapter = get_adapter(current_agent)

        # Collect events — buffer text for quality check, yield non-error immediately
        last_error = None
        collected_text = []
        async for ev in adapter.invoke(
            prompt=prompt,
            system_prompt=system_prompt,
            session_id=current_session,
            model=current_model,
            timeout=timeout,
        ):
            if ev.type == "error":
                last_error = ev.data.get("message", "")
                break
            if ev.type == "text":
                collected_text.append(ev.data.get("delta", ""))
            yield ev

        # No error — check quality before finishing
        if last_error is None:
            # Quality escalation: if response is too weak for complexity,
            # retry with a stronger model (one attempt only)
            if not already_escalated and collected_text:
                full_response = "".join(collected_text)
                if should_escalate(full_response, complexity, current_model):
                    escalation = get_escalation(current_model)
                    if escalation:
                        esc_agent, esc_model = escalation
                        already_escalated = True
                        logger.warning(
                            "response too weak for %s task (%d chars), "
                            "escalating %s → %s",
                            complexity, len(full_response),
                            current_model or "default", esc_model or "default",
                        )
                        yield AgentEvent("text", {
                            "delta": f"\n\n🔄 Escalating to stronger model...\n\n",
                        })
                        current_agent = esc_agent
                        current_model = esc_model
                        current_session = None
                        tried.discard(esc_agent)
                        collected_text.clear()
                        continue
            return

        # Transient claude CLI bug (duplicate tool_use id on parallel calls).
        # Single retry with fresh session — upstream issue, bug is intermittent.
        # Refs: anthropics/claude-code#20508, #18131, #6836.
        if is_transient(last_error) and not transient_retried:
            transient_retried = True
            logger.warning(
                "transient CLI bug on %s (%s) — retrying fresh",
                current_agent, last_error[:120],
            )
            current_session = None
            tried.discard(current_agent)
            collected_text.clear()
            continue

        # Context overflow with a session? Drop session and retry same adapter
        if is_context_overflow(last_error) and current_session and not dropped_session:
            logger.warning(
                "context overflow on %s with session %s — retrying without session",
                current_agent, current_session[:12],
            )
            yield AgentEvent("text", {
                "delta": "\n\n🔄 Session too large — starting fresh...\n\n",
            })
            current_session = None
            dropped_session = True
            tried.discard(current_agent)
            collected_text.clear()
            continue

        # Check if this is a rate limit we can fallback from
        fallback_agent = None
        if is_rate_limited(last_error):
            fallback_agent = get_fallback(current_agent, tried)

        if not fallback_agent:
            yield AgentEvent("error", {"message": last_error})
            return

        # Notify user and retry with fallback
        logger.warning(
            "rate limited on %s, falling back to %s (error: %s)",
            current_agent, fallback_agent, last_error[:200],
        )
        yield AgentEvent("text", {
            "delta": f"\n\n⚡ {current_agent} rate limited — switching to {fallback_agent}...\n\n",
        })

        current_agent = fallback_agent
        current_model = None
        current_session = None
        collected_text.clear()


async def _prepend_text(prefix: str, events):
    """Inject a text event before the adapter's stream."""
    yield AgentEvent("text", {"delta": prefix})
    async for ev in events:
        yield ev


async def _cancel_guard(job_id: int, events):
    """Wrap an adapter event stream; abort cleanly if cancel_requested flips."""
    async for ev in events:
        if queue.is_cancel_requested(job_id):
            logger.info("job %d: cancel_requested, aborting stream", job_id)
            yield AgentEvent("cancelled", {"message": "(interrupted — new message)"})
            return
        yield ev


PERSONA_EXPIRY_SECONDS = 24 * 3600  # 24 hours


def _check_persona(user_id: int) -> str | None:
    """Return active persona name if set and not expired. Auto-clears if expired."""
    result = queue.get_user_state_with_ts(user_id, "active_persona")
    if not result:
        return None
    name, updated_at = result
    import time
    if (time.time() - updated_at) > PERSONA_EXPIRY_SECONDS:
        logger.info("persona %s expired for user %s (%.1fh old), auto-clearing",
                     name, user_id, (time.time() - updated_at) / 3600)
        queue.delete_user_state(user_id, "active_persona")
        return None
    return name


def _build_prompt(job: queue.Job) -> tuple[str, Optional[str], Optional[int], Optional[str]]:
    """Returns (full_prompt_for_agent, original_user_text_or_none, user_id, model)."""
    payload = job.payload
    user_id = payload.get("user_id")
    model = payload.get("model")

    if job.kind == "chat":
        text = payload["text"]
        complexity = payload.get("complexity")
        return enrich_chat_prompt(user_id, text, agent=job.agent, model=model,
                                  complexity=complexity), text, user_id, model

    if job.kind == "briefing":
        return enrich_briefing_prompt(), None, user_id, model

    if job.kind == "checkin":
        return enrich_checkin_prompt(), None, user_id, model

    if job.kind == "reflection":
        # Caller builds full prompt; no extra enrichment
        return payload["prompt"], None, user_id, model

    if job.kind == "raw":
        return payload["prompt"], None, user_id, model

    raise ValueError(f"unknown job kind: {job.kind}")

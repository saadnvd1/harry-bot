"""OpenAI Codex CLI adapter.

Codex's `--json` mode emits JSONL events but does NOT stream text deltas —
the full text arrives as a single `item.completed` event. Adapter maps to
one `text` event then `done`.

Auth: managed by the codex CLI itself (`codex login`). No API key needed if
you've already signed in to ChatGPT.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from typing import AsyncIterator

from agents.base import AgentAdapter, AgentEvent, register_adapter

logger = logging.getLogger(__name__)


class CodexAdapter:
    name = "codex"
    capabilities = {"sessions"}   # text comes in one chunk, not streamed

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        timeout: int = 600,
    ) -> AsyncIterator[AgentEvent]:
        if shutil.which("codex") is None:
            yield AgentEvent("error", {
                "message": "codex CLI not installed. Install with: npm install -g @openai/codex (or equivalent)."
            })
            return

        cmd = ["codex"]
        if session_id:
            cmd += ["exec", "resume", session_id, "--json"]
        else:
            cmd += ["exec", "--json"]
        if model:
            cmd += ["-m", model]
        if system_prompt:
            # Codex has no --system-prompt flag; we inline as a prefix
            prompt = f"[System context]\n{system_prompt}\n\n[User]\n{prompt}"
        cmd.append(prompt)

        logger.info("codex: session=%s, model=%s, prompt_len=%d",
                    session_id or "new", model or "default", len(prompt))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )

        emitted_session = False
        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    # Preamble lines (Reading additional input…) aren't JSON — skip
                    continue

                t = event.get("type")
                if t == "thread.started":
                    if not emitted_session:
                        emitted_session = True
                        yield AgentEvent("session_start", {
                            "session_id": event.get("thread_id")
                        })
                elif t == "item.completed":
                    item = event.get("item", {})
                    if item.get("type") == "agent_message":
                        text = item.get("text", "")
                        if text:
                            yield AgentEvent("text", {"delta": text})
                    elif item.get("type") == "tool_call":
                        yield AgentEvent("tool_call", {
                            "name": item.get("name", "?"),
                            "id": item.get("id"),
                            "input": item.get("input", {}),
                        })
                elif t == "turn.completed":
                    yield AgentEvent("done", {
                        "stop_reason": "end_turn",
                        "usage": event.get("usage", {}),
                    })
                    return
                elif t == "error":
                    yield AgentEvent("error", {
                        "message": event.get("message", str(event)),
                    })
                    return

            yield AgentEvent("done", {})

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            yield AgentEvent("error", {"message": f"codex timeout after {timeout}s"})
        except Exception as e:
            logger.exception("codex failed")
            yield AgentEvent("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()


register_adapter("codex", CodexAdapter)

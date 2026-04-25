"""OpenCode CLI adapter.

OpenCode is a multi-provider CLI — can route to OpenAI, Anthropic, local
Ollama/LM Studio, etc. We invoke with `opencode run --format json`. Output
is NDJSON with `step_start`, `text`, `tool`, `step_finish` events and a
`sessionID` field on every event.

Streaming: text events arrive as the model generates. OpenCode supports
session resume via `-s <session_id>`.
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


class OpenCodeAdapter:
    name = "opencode"
    capabilities = {"streaming", "sessions", "tools"}

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        timeout: int = 600,
    ) -> AsyncIterator[AgentEvent]:
        if shutil.which("opencode") is None:
            yield AgentEvent("error", {
                "message": "opencode CLI not installed. See https://opencode.ai"
            })
            return

        cmd = ["opencode", "run", "--format", "json"]
        if session_id:
            cmd += ["-s", session_id]
        if model:
            cmd += ["-m", model]
        # OpenCode has no --system-prompt; inline as prefix
        if system_prompt:
            prompt = f"[System context]\n{system_prompt}\n\n[User]\n{prompt}"
        cmd.append(prompt)

        logger.info("opencode: session=%s, model=%s, prompt_len=%d",
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
                    continue

                # Every event carries sessionID — pick it up on first sight
                sid = event.get("sessionID")
                if sid and not emitted_session:
                    emitted_session = True
                    yield AgentEvent("session_start", {"session_id": sid})

                t = event.get("type")
                part = event.get("part", {})
                ptype = part.get("type", "")

                if t == "text":
                    # OpenCode text events contain the full part text each time;
                    # diff against last-seen to get delta. Simpler: just emit as delta.
                    text = part.get("text", "")
                    if text:
                        yield AgentEvent("text", {"delta": text})

                elif t == "tool" or ptype == "tool-call":
                    tool_name = part.get("tool") or part.get("name") or "?"
                    yield AgentEvent("tool_call", {
                        "name": tool_name,
                        "id": part.get("id"),
                        "input": part.get("input", {}),
                    })

                elif t == "step_finish":
                    tokens = part.get("tokens") or {}
                    yield AgentEvent("done", {
                        "stop_reason": part.get("reason"),
                        "usage": {
                            "input_tokens": tokens.get("input"),
                            "output_tokens": tokens.get("output"),
                            "cache_read": (tokens.get("cache") or {}).get("read"),
                            "cost_usd": part.get("cost"),
                        },
                    })
                    return

                elif t == "error":
                    yield AgentEvent("error", {
                        "message": event.get("message", str(event)),
                    })
                    return

            # Stream ended without step_finish
            yield AgentEvent("done", {})

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            yield AgentEvent("error", {"message": f"opencode timeout after {timeout}s"})
        except Exception as e:
            logger.exception("opencode failed")
            yield AgentEvent("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()


register_adapter("opencode", OpenCodeAdapter)

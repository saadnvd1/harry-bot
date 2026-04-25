"""Claude Code CLI adapter. Parses `claude --print --output-format stream-json`
NDJSON and emits normalized AgentEvents.

Uses Max-subscription CLI (no API key). Full streaming capability — all event
types populated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import AsyncIterator

from agents.base import AgentAdapter, AgentEvent, register_adapter

logger = logging.getLogger(__name__)


class ClaudeAdapter:
    name = "claude"
    capabilities = {"streaming", "tools", "sessions"}

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        timeout: int = 600,
    ) -> AsyncIterator[AgentEvent]:
        cmd = [
            "claude", "--print",
            "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])
        if session_id:
            cmd.extend(["--resume", session_id])
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)

        env = {**os.environ, "IS_SANDBOX": "1"}
        logger.info("claude adapter: spawning (model=%s, session=%s, prompt_len=%d)",
                    model or "default", session_id or "new", len(prompt))

        # Use larger buffer limit for stdout (default 64KB too small for image base64)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=16 * 1024 * 1024,  # 16MB line limit for image streaming
        )

        tool_inputs: dict[int, dict] = {}  # content_block index → {name, id, json_buf}
        emitted_session = False
        stop_reason: str | None = None
        usage: dict | None = None

        try:
            async for event in self._iter_events(proc, timeout):
                etype = event.get("type")

                if etype == "system" and event.get("subtype") == "init":
                    sid = event.get("session_id")
                    if sid and not emitted_session:
                        emitted_session = True
                        yield AgentEvent("session_start", {"session_id": sid})

                elif etype == "stream_event":
                    se = event.get("event", {})
                    se_type = se.get("type")

                    if se_type == "message_start" and not emitted_session:
                        msg = se.get("message", {})
                        sid = msg.get("id")
                        if sid:
                            emitted_session = True
                            yield AgentEvent("session_start", {"session_id": sid})

                    elif se_type == "content_block_start":
                        idx = se.get("index", 0)
                        block = se.get("content_block", {})
                        if block.get("type") == "tool_use":
                            tool_inputs[idx] = {
                                "name": block.get("name", "?"),
                                "id": block.get("id"),
                                "json_buf": "",
                            }

                    elif se_type == "content_block_delta":
                        idx = se.get("index", 0)
                        delta = se.get("delta", {})
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield AgentEvent("text", {"delta": text})
                        elif dtype == "input_json_delta":
                            if idx in tool_inputs:
                                tool_inputs[idx]["json_buf"] += delta.get("partial_json", "")

                    elif se_type == "content_block_stop":
                        idx = se.get("index", 0)
                        if idx in tool_inputs:
                            info = tool_inputs.pop(idx)
                            parsed = {}
                            try:
                                parsed = json.loads(info["json_buf"]) if info["json_buf"] else {}
                            except json.JSONDecodeError:
                                parsed = {"_raw": info["json_buf"]}
                            yield AgentEvent("tool_call", {
                                "name": info["name"],
                                "id": info["id"],
                                "input": parsed,
                            })

                    elif se_type == "message_delta":
                        delta = se.get("delta", {})
                        if "stop_reason" in delta:
                            stop_reason = delta["stop_reason"]
                        if "usage" in se:
                            usage = se["usage"]

                elif etype == "assistant":
                    # Non-partial assistant message (when --include-partial-messages
                    # is off, or summary events). Skip if we already streamed deltas.
                    pass

                elif etype == "result":
                    if event.get("is_error"):
                        yield AgentEvent("error", {
                            "message": event.get("result", "unknown claude error"),
                        })
                        return
                    if event.get("total_cost_usd") is not None:
                        usage = usage or {}
                        usage["cost_usd"] = event["total_cost_usd"]

                elif etype == "error":
                    yield AgentEvent("error", {"message": str(event)})
                    return

            # Stream exhausted normally
            yield AgentEvent("done", {"stop_reason": stop_reason, "usage": usage or {}})

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            yield AgentEvent("error", {"message": f"timeout after {timeout}s"})
        except Exception as e:
            logger.exception("claude adapter failed")
            yield AgentEvent("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

            rc = proc.returncode
            if rc and rc != 0:
                stderr = b""
                try:
                    stderr = await proc.stderr.read()
                except Exception:
                    pass
                logger.warning("claude exited rc=%d stderr=%s", rc, stderr[:300])

    async def _iter_events(self, proc, timeout: int):
        """Yield parsed JSON events from stdout until EOF or timeout."""
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
                return
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("claude stream: unparseable line: %s (%s)", line[:200], e)


register_adapter("claude", ClaudeAdapter)

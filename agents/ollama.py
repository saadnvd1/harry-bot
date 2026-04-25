"""Ollama adapter — local, free LLM via HTTP API.

Uses Ollama's streaming /api/generate endpoint (not the CLI, which adds
terminal escape codes). Zero cost per call, ~200ms-5s latency for 3B models
on CPU. No tool support, no session resume — this is for trivial factual
queries where Haiku would be overkill.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import urllib.error
import urllib.request
import asyncio

from agents.base import AgentAdapter, AgentEvent, register_adapter

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:3b"

# Ollama is used for trivial lookups — skip the full Harry personality prompt
# (it's long, slows a 3B CPU model, and isn't useful for facts/math).
MINIMAL_SYSTEM = (
    "You are a terse factual assistant. Answer in 1-3 sentences, no preamble, "
    "no apologies. For math/units/conversions, give the number + units directly."
)


class OllamaAdapter:
    name = "ollama"
    capabilities = {"streaming"}

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        session_id: str | None = None,   # ignored — Ollama is stateless
        model: str | None = None,
        timeout: int = 120,
    ) -> AsyncIterator[AgentEvent]:
        model_id = model or DEFAULT_MODEL
        logger.info("ollama: model=%s, prompt_len=%d", model_id, len(prompt))

        # Deliberately ignore the full system prompt — small model, trivial task
        payload = {
            "model": model_id,
            "prompt": prompt,
            "system": MINIMAL_SYSTEM,
            "stream": True,
            "keep_alive": "24h",  # don't unload model between requests (default 5m)
        }

        try:
            # Run blocking HTTP call off the loop so we can stream
            async for chunk in self._stream(payload, timeout):
                yield chunk
        except urllib.error.URLError as e:
            logger.warning("ollama unreachable: %s", e)
            yield AgentEvent("error", {
                "message": f"Ollama unavailable: {e}. Is the service running? (`sm status ollama` or `systemctl status ollama`)",
            })
        except Exception as e:
            logger.exception("ollama failed")
            yield AgentEvent("error", {"message": f"{type(e).__name__}: {e}"})

    async def _stream(self, payload: dict, timeout: int) -> AsyncIterator[AgentEvent]:
        loop = asyncio.get_event_loop()

        def _open():
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=timeout)

        try:
            resp = await loop.run_in_executor(None, _open)
        except Exception as e:
            yield AgentEvent("error", {"message": f"ollama connect: {e}"})
            return

        try:
            # Read line-by-line off the loop
            while True:
                line = await loop.run_in_executor(None, resp.readline)
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = event.get("response", "")
                if text:
                    yield AgentEvent("text", {"delta": text})
                if event.get("done"):
                    yield AgentEvent("done", {
                        "stop_reason": event.get("done_reason"),
                        "usage": {
                            "input_tokens": event.get("prompt_eval_count"),
                            "output_tokens": event.get("eval_count"),
                        },
                    })
                    break
        finally:
            try:
                resp.close()
            except Exception:
                pass


register_adapter("ollama", OllamaAdapter)

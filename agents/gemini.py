"""Gemini adapter — Google's free-tier API for simple/ack responses.

Uses the REST streaming endpoint directly (no SDK dependency).
Free tier: 15 RPM, 1M TPM for gemini-2.5-flash — more than enough
for a personal bot's simple queries.

Fallback: if rate-limited or unavailable, router sends to haiku.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
import asyncio
from typing import AsyncIterator

from agents.base import AgentAdapter, AgentEvent, register_adapter

logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY", "")
DEFAULT_MODEL = "gemini-2.5-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Minimal system prompt — same philosophy as ollama adapter
MINIMAL_SYSTEM = (
    "You are a terse, friendly assistant. Answer in 1-3 sentences, no preamble, "
    "no apologies. For math/units/conversions, give the number + units directly."
)


class GeminiAdapter:
    name = "gemini"
    capabilities = {"streaming"}

    async def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        session_id: str | None = None,   # ignored — stateless
        model: str | None = None,
        timeout: int = 30,
    ) -> AsyncIterator[AgentEvent]:
        if not GEMINI_API_KEY:
            yield AgentEvent("error", {"message": "GEMINI_API_KEY not set"})
            return

        model_id = model or DEFAULT_MODEL
        logger.info("gemini: model=%s, prompt_len=%d", model_id, len(prompt))

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "systemInstruction": {"parts": [{"text": MINIMAL_SYSTEM}]},
        }

        url = f"{API_BASE}/models/{model_id}:streamGenerateContent?alt=sse&key={GEMINI_API_KEY}"

        try:
            async for chunk in self._stream(url, payload, timeout):
                yield chunk
        except Exception as e:
            logger.exception("gemini failed")
            yield AgentEvent("error", {"message": f"{type(e).__name__}: {e}"})

    async def _stream(self, url: str, payload: dict, timeout: int) -> AsyncIterator[AgentEvent]:
        loop = asyncio.get_event_loop()

        def _open():
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=timeout)

        try:
            resp = await loop.run_in_executor(None, _open)
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            # Surface rate limit errors so fallback system catches them
            if e.code == 429:
                yield AgentEvent("error", {"message": f"rate limit: {body}"})
            else:
                yield AgentEvent("error", {"message": f"gemini HTTP {e.code}: {body}"})
            return
        except urllib.error.URLError as e:
            yield AgentEvent("error", {"message": f"gemini unreachable: {e}"})
            return

        input_tokens = 0
        output_tokens = 0

        try:
            # Gemini SSE: lines starting with "data: " contain JSON
            while True:
                line = await loop.run_in_executor(None, resp.readline)
                if not line:
                    break
                line = line.decode("utf-8").strip()
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]  # strip "data: " prefix
                if data_str == "[DONE]":
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # Extract text from candidates
                candidates = event.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        text = part.get("text", "")
                        if text:
                            yield AgentEvent("text", {"delta": text})

                # Extract usage metadata
                usage = event.get("usageMetadata", {})
                if usage:
                    input_tokens = usage.get("promptTokenCount", input_tokens)
                    output_tokens = usage.get("candidatesTokenCount", output_tokens)

            yield AgentEvent("done", {
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            })
        finally:
            try:
                resp.close()
            except Exception:
                pass


register_adapter("gemini", GeminiAdapter)

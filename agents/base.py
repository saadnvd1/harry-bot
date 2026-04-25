"""Normalized agent event schema + adapter registry.

Event taxonomy is narrow so CLIs without full streaming (Codex, aider) can
degrade gracefully — just emit `text` + `done` and renderers still work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Literal, Protocol

EventType = Literal[
    "session_start",   # data: {session_id}
    "text",            # data: {delta} — text chunk for user
    "tool_call",       # data: {name, input, id} — tool invocation (rendered inline)
    "error",           # data: {message}
    "cancelled",       # data: {message} — worker aborted (steer queue)
    "done",            # data: {stop_reason?, usage?}
]


@dataclass
class AgentEvent:
    type: EventType
    data: dict = field(default_factory=dict)


class AgentAdapter(Protocol):
    """All adapters implement this. Consumers iterate events async."""

    name: str
    capabilities: set[str]  # {"streaming", "tools", "sessions"}

    def invoke(
        self,
        prompt: str,
        system_prompt: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        timeout: int = 600,
    ) -> AsyncIterator[AgentEvent]:
        ...


_REGISTRY: dict[str, Callable[[], AgentAdapter]] = {}


def register_adapter(name: str, factory: Callable[[], AgentAdapter]) -> None:
    _REGISTRY[name] = factory


def get_adapter(name: str) -> AgentAdapter:
    if name not in _REGISTRY:
        raise ValueError(f"unknown agent adapter: {name}. known: {list(_REGISTRY)}")
    return _REGISTRY[name]()

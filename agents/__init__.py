"""CLI-agnostic agent adapters. Each adapter wraps a coding-agent CLI
(Claude Code, Codex, OpenCode, etc.) and emits normalized AgentEvents."""

from agents.base import AgentEvent, AgentAdapter, get_adapter, register_adapter

__all__ = ["AgentEvent", "AgentAdapter", "get_adapter", "register_adapter"]

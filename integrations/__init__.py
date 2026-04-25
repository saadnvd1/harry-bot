"""Integration plugin system — each service (Gmail, Drive, Notion, etc.) is
a manifest-driven plugin. Discriminated by type:
`python-tool` (cheap, CLI-agnostic) or `mcp` (flexible, Claude-only).

Default everything to `python-tool` for token efficiency.
"""

from integrations.registry import (
    Integration,
    load_integrations,
    get_prompt_addendum,
    get_integration,
)

__all__ = [
    "Integration",
    "load_integrations",
    "get_prompt_addendum",
    "get_integration",
]

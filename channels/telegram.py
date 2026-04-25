"""Telegram renderer. Consumes an AgentEvent stream and edits a single message
as text streams in. Tool calls rendered inline like Claude Code's TUI.

Respects Telegram's ~1 edit/sec/message rate limit via debouncing.
Uses HTML parse mode with markdown→HTML conversion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from typing import AsyncIterator

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter

from agents.base import AgentEvent

logger = logging.getLogger(__name__)

MAX_MSG_LEN = 4000              # stay under Telegram's 4096 limit with buffer
MIN_SPLIT_RATIO = 0.3           # if natural break is earlier than limit*ratio, hard-cut instead
EDIT_DEBOUNCE_SECONDS = 0.8     # batch text_delta edits
TOOL_EMOJI = "🔧"
THINKING_PLACEHOLDER = "…"
CONTINUED_MARKER = "↪"          # leading marker on a spawned follow-up message


# ========== Markdown → Telegram HTML conversion ==========

def _escape_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _strip_md(s: str) -> str:
    """Strip markdown inline formatting from text."""
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'~~(.+?)~~', r'\1', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    return s.strip()


def _render_table_box(table_lines: list[str]) -> str:
    """Convert markdown pipe-table to compact aligned text for <pre> display."""

    def dw(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)

    rows: list[list[str]] = []
    has_sep = False
    for line in table_lines:
        cells = [_strip_md(c) for c in line.strip().strip('|').split('|')]
        if all(re.match(r'^:?-+:?$', c) for c in cells if c):
            has_sep = True
            continue
        rows.append(cells)
    if not rows or not has_sep:
        return '\n'.join(table_lines)

    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([''] * (ncols - len(r)))
    widths = [max(dw(r[c]) for r in rows) for c in range(ncols)]

    def dr(cells: list[str]) -> str:
        return '  '.join(f'{c}{" " * (w - dw(c))}' for c, w in zip(cells, widths))

    out = [dr(rows[0])]
    out.append('  '.join('─' * w for w in widths))
    for row in rows[1:]:
        out.append(dr(row))
    return '\n'.join(out)


def markdown_to_html(text: str) -> str:
    """Convert markdown to Telegram-safe HTML."""
    if not text:
        return ""

    # 1. Extract and protect code blocks
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)

    # 1.5. Convert markdown tables to box-drawing
    lines = text.split('\n')
    rebuilt: list[str] = []
    li = 0
    while li < len(lines):
        if re.match(r'^\s*\|.+\|', lines[li]):
            tbl: list[str] = []
            while li < len(lines) and re.match(r'^\s*\|.+\|', lines[li]):
                tbl.append(lines[li])
                li += 1
            box = _render_table_box(tbl)
            if box != '\n'.join(tbl):
                code_blocks.append(box)
                rebuilt.append(f"\x00CB{len(code_blocks) - 1}\x00")
            else:
                rebuilt.extend(tbl)
        else:
            rebuilt.append(lines[li])
            li += 1
    text = '\n'.join(rebuilt)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"
    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # 3. Headers → bold text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'**\1**', text, flags=re.MULTILINE)

    # 4. Blockquotes > text → just the text
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    text = _escape_html(text)

    # 6. Links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 10. Bullet lists
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 11. Restore inline code
    for i, code in enumerate(inline_codes):
        escaped = _escape_html(code)
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks
    for i, code in enumerate(code_blocks):
        escaped = _escape_html(code)
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def _split_at(text: str, limit: int) -> tuple[str, str]:
    """Return (before, after) with before + after == text and len(before) <= limit.

    Prefers natural break points (paragraph > line > sentence > word). Falls back
    to hard cut at `limit` if no break is far enough through the chunk.

    Never loses content — the break character stays at the START of `after` so
    it's visible on the next message (e.g., a `\\n\\n` between paragraphs).
    """
    if len(text) <= limit:
        return text, ""
    min_idx = int(limit * MIN_SPLIT_RATIO)

    # Prefer paragraph break (double newline)
    idx = text.rfind("\n\n", min_idx, limit)
    if idx != -1:
        return text[:idx], text[idx:]

    # Then single newline
    idx = text.rfind("\n", min_idx, limit)
    if idx != -1:
        return text[:idx], text[idx:]

    # Then sentence end (keep the punctuation on the `before` side)
    for end in (". ", "! ", "? "):
        idx = text.rfind(end, min_idx, limit)
        if idx != -1:
            return text[: idx + len(end) - 1], text[idx + len(end) - 1 :]

    # Then word boundary (space)
    idx = text.rfind(" ", min_idx, limit)
    if idx != -1:
        return text[:idx], text[idx:]

    # Hard cut — no good break available
    return text[:limit], text[limit:]


def _tool_summary(name: str, inp: dict) -> str:
    """Compact one-line summary of a tool call for inline render (HTML)."""
    def code(s: str) -> str:
        return f"<code>{_escape_html(s)}</code>"

    if name == "Bash":
        cmd = inp.get("command", "")
        return f"{TOOL_EMOJI} Bash: {code(cmd[:200])}"
    if name == "Read":
        fp = inp.get("file_path", "?")
        return f"{TOOL_EMOJI} Read: {code(fp)}"
    if name == "Edit" or name == "Write":
        fp = inp.get("file_path", "?")
        return f"{TOOL_EMOJI} {name}: {code(fp)}"
    if name == "Glob":
        return f"{TOOL_EMOJI} Glob: {code(inp.get('pattern', '?'))}"
    if name == "Grep":
        p = inp.get("pattern", "?")
        return f"{TOOL_EMOJI} Grep: {code(p[:100])}"
    if name == "WebFetch":
        return f"{TOOL_EMOJI} WebFetch: {code(inp.get('url', '?'))}"
    if name == "WebSearch":
        return f"{TOOL_EMOJI} WebSearch: {code(inp.get('query', '?')[:100])}"
    # Fallback: compact JSON
    try:
        preview = json.dumps(inp, separators=(",", ":"))[:150]
    except Exception:
        preview = str(inp)[:150]
    return f"{TOOL_EMOJI} {name}: {code(preview)}"


class TelegramRenderer:
    """Holds a live Telegram message handle and buffers/flushes edits."""

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.msg_ids: list[int] = []          # [oldest, ..., current]; current is last
        self.committed_body_len = 0           # chars of body frozen in prior messages
        self.text_buf = ""           # streamed assistant text
        self.tool_lines: list[str] = []   # rendered tool calls in order
        self.last_edit_at = 0.0
        self.pending_edit = False
        self.session_id: str | None = None

    @property
    def msg_id(self) -> int | None:
        """Current (most recently spawned) Telegram message id, or None."""
        return self.msg_ids[-1] if self.msg_ids else None

    async def start(self) -> int:
        """Send initial placeholder so later edits have a target."""
        msg = await self.bot.send_message(
            chat_id=self.chat_id, text=THINKING_PLACEHOLDER
        )
        self.msg_ids.append(msg.message_id)
        return msg.message_id

    async def _spawn_next(self, initial_text: str) -> None:
        """Send a new message to continue rendering into. Preserves a leading
        continuation marker so the user can see it's a follow-up, not a new reply."""
        text = initial_text.lstrip() or THINKING_PLACEHOLDER
        prefixed = f"{CONTINUED_MARKER} {text}" if text != THINKING_PLACEHOLDER else text
        try:
            msg = await self.bot.send_message(
                chat_id=self.chat_id, text=prefixed, parse_mode=ParseMode.HTML,
            )
        except BadRequest:
            # Fall back to plain text (markdown parse failure shouldn't block split)
            msg = await self.bot.send_message(chat_id=self.chat_id, text=prefixed)
        self.msg_ids.append(msg.message_id)

    def _render(self) -> str:
        """Full current body (tool lines + text) as Telegram HTML.
        Tool lines are already HTML. Text buf is markdown → converted here."""
        parts = []
        if self.tool_lines:
            # Tool lines are already HTML-formatted
            parts.append("\n".join(self.tool_lines))
        if self.text_buf:
            text = self.text_buf.strip()
            if text:
                parts.append(markdown_to_html(text))
        if not parts:
            return THINKING_PLACEHOLDER
        return "\n\n".join(parts)

    async def _edit_current(self, text: str) -> None:
        """Edit the currently-active message (last in msg_ids) with `text`."""
        if not self.msg_ids:
            return
        target_id = self.msg_ids[-1]
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=target_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg:
                return
            if "can't parse" in msg or "parse entities" in msg:
                try:
                    await self.bot.edit_message_text(
                        chat_id=self.chat_id,
                        message_id=target_id,
                        text=text,
                    )
                except Exception as inner:
                    logger.warning("plain edit failed: %s", inner)
            elif "message is too long" in msg:
                # Shouldn't happen after our split, but be defensive
                logger.warning("telegram rejected as too long even after split: %d chars", len(text))
            else:
                logger.warning("telegram edit failed: %s", e)
        except RetryAfter as e:
            wait = getattr(e, "retry_after", 2)
            logger.info("telegram rate limited, sleeping %ss", wait)
            await asyncio.sleep(wait + 0.5)
            await self._edit_current(text)
        except Exception as e:
            logger.warning("telegram edit unexpected error: %s", e)

    async def _flush_edit(self, final: bool = False) -> None:
        if not self.msg_ids:
            return
        body = self._render()
        current = body[self.committed_body_len:]

        # Split-and-spawn loop — handle arbitrary-length overflow.
        while len(current) > MAX_MSG_LEN:
            before, after = _split_at(current, MAX_MSG_LEN)
            # Finalize current message with `before`
            await self._edit_current(before)
            self.committed_body_len += len(before)
            # Open a new message for the rest
            await self._spawn_next(after if len(after) <= MAX_MSG_LEN else after[:MAX_MSG_LEN])
            current = after

        # Edit the (possibly new) current message with the remaining content.
        # Skip empty edits — placeholder from _spawn_next is fine until more text arrives.
        if current:
            await self._edit_current(current)

        self.last_edit_at = time.monotonic()
        self.pending_edit = False

    async def _maybe_flush(self) -> None:
        now = time.monotonic()
        if (now - self.last_edit_at) >= EDIT_DEBOUNCE_SECONDS:
            await self._flush_edit()

    async def consume(self, events: AsyncIterator[AgentEvent]) -> dict:
        """Drive the event stream. Returns summary dict."""
        error: str | None = None
        stop_reason: str | None = None
        usage: dict = {}
        cancelled = False

        if self.msg_id is None:
            await self.start()

        async for ev in events:
            if ev.type == "session_start":
                self.session_id = ev.data.get("session_id")

            elif ev.type == "text":
                self.text_buf += ev.data.get("delta", "")
                await self._maybe_flush()

            elif ev.type == "tool_call":
                line = _tool_summary(ev.data.get("name", "?"), ev.data.get("input", {}))
                # If there's already text before this tool call, add visual break
                if self.text_buf.strip():
                    self.text_buf = self.text_buf.rstrip() + "\n\n"
                self.tool_lines.append(line)
                # Always flush on tool call — visual beat, rare enough
                await self._flush_edit()

            elif ev.type == "cancelled":
                cancelled = True
                msg = ev.data.get("message", "(interrupted)")
                self.text_buf = (self.text_buf.rstrip() + f"\n\n_{msg}_").strip()
                await self._flush_edit(final=True)
                break

            elif ev.type == "error":
                error = ev.data.get("message", "unknown error")
                self.text_buf += f"\n\n⚠️ {error}"
                await self._flush_edit(final=True)
                break

            elif ev.type == "done":
                stop_reason = ev.data.get("stop_reason")
                usage = ev.data.get("usage") or {}

        # Final flush (even if debounce hadn't elapsed)
        await self._flush_edit(final=True)

        return {
            "text": self.text_buf,
            "tool_count": len(self.tool_lines),
            "tool_lines": list(self.tool_lines),
            "session_id": self.session_id,
            "stop_reason": stop_reason,
            "usage": usage,
            "error": error,
            "cancelled": cancelled,
        }

    async def mark_failed(self, reason: str) -> None:
        """Used when consume() itself throws — update the placeholder."""
        if not self.msg_ids:
            try:
                await self.bot.send_message(chat_id=self.chat_id, text=f"⚠️ {reason}")
            except Exception:
                pass
            return
        self.text_buf = (self.text_buf + f"\n\n⚠️ {reason}").strip()
        await self._flush_edit(final=True)

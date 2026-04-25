#!/usr/bin/env python3
"""Search past conversations.

Usage:
    python3 tools/search_conversations.py "deploy issue"
    python3 tools/search_conversations.py "workout" --days 7
    python3 tools/search_conversations.py "angry" --role user
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
CONVERSATIONS_DIR = VAULT_PATH / "conversations"


def search(query: str, days: int = 14, role: str = "", max_results: int = 20) -> list[dict]:
    """Search conversations for a query string. Returns matching messages with context."""
    if not CONVERSATIONS_DIR.exists():
        return []

    keywords = [w.lower() for w in re.findall(r'\w+', query) if len(w) > 2]
    if not keywords:
        return []

    results = []
    today = datetime.now()

    for i in range(days):
        date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        fpath = CONVERSATIONS_DIR / f"{date_str}.json"
        if not fpath.exists():
            continue

        try:
            data = json.loads(fpath.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        for _uid, msgs in data.items():
            for idx, msg in enumerate(msgs):
                msg_role = msg.get("role", "")
                if role and msg_role != role:
                    continue

                content = (msg.get("content") or "").lower()
                # Score by keyword matches
                score = sum(content.count(kw) for kw in keywords)
                if score == 0:
                    continue

                # Get surrounding context (1 message before, 1 after)
                context_msgs = []
                if idx > 0:
                    prev = msgs[idx - 1]
                    context_msgs.append({
                        "role": "User" if prev["role"] == "user" else "Harry",
                        "text": (prev.get("content") or "")[:150],
                    })
                context_msgs.append({
                    "role": "User" if msg_role == "user" else "Harry",
                    "text": (msg.get("content") or "")[:300],
                    "match": True,
                })
                if idx < len(msgs) - 1:
                    nxt = msgs[idx + 1]
                    context_msgs.append({
                        "role": "User" if nxt["role"] == "user" else "Harry",
                        "text": (nxt.get("content") or "")[:150],
                    })

                ts = msg.get("ts", "")
                time_str = ""
                if ts:
                    try:
                        time_str = datetime.fromisoformat(ts).strftime("%b %d %I:%M %p")
                    except Exception:
                        pass

                results.append({
                    "date": date_str,
                    "time": time_str,
                    "score": score,
                    "messages": context_msgs,
                })

    # Sort by score descending, then by date descending
    results.sort(key=lambda r: (-r["score"], r["date"]), reverse=False)
    return results[:max_results]


def format_results(results: list[dict], query: str) -> str:
    if not results:
        return f"No conversations found matching '{query}'."

    lines = [f"Found {len(results)} match{'es' if len(results) != 1 else ''} for '{query}':\n"]
    for r in results:
        lines.append(f"--- {r['date']} {r['time']} ---")
        for msg in r["messages"]:
            prefix = "→ " if msg.get("match") else "  "
            lines.append(f"{prefix}{msg['role']}: {msg['text']}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search Harry conversations")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--days", type=int, default=14, help="Days to search back (default 14)")
    parser.add_argument("--role", choices=["user", "assistant"], default="", help="Filter by role")
    parser.add_argument("--max", type=int, default=20, help="Max results")
    args = parser.parse_args()

    results = search(args.query, days=args.days, role=args.role, max_results=args.max)
    print(format_results(results, args.query))

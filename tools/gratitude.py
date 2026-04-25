#!/usr/bin/env python3
"""
Gratitude CLI — track what you're grateful for.
Stores data in vault/gratitude/ as daily JSON files.

Usage:
    python gratitude.py add "grateful for morning coffee"
    python gratitude.py list [--date 2026-04-17] [--days 7]
    python gratitude.py streak
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import random
import string

# Default paths
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
GRATITUDE_PATH = VAULT_PATH / "gratitude"


def generate_id() -> str:
    """Generate a short random ID."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=7))


def get_file_path(date: str) -> Path:
    """Get the JSON file path for a given date."""
    return GRATITUDE_PATH / f"{date}.json"


def load_day(date: str) -> dict:
    """Load gratitude entries for a specific date."""
    filepath = get_file_path(date)
    if filepath.exists():
        return json.loads(filepath.read_text())
    return {"date": date, "entries": []}


def save_day(data: dict) -> None:
    """Save gratitude entries for a day."""
    GRATITUDE_PATH.mkdir(parents=True, exist_ok=True)
    filepath = get_file_path(data["date"])
    filepath.write_text(json.dumps(data, indent=2))


def git_commit(message: str) -> None:
    """Auto-commit vault changes."""
    try:
        subprocess.run(["git", "add", "-A"], cwd=str(VAULT_PATH), capture_output=True, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(VAULT_PATH), capture_output=True, timeout=10
        )
    except Exception:
        pass  # Non-critical


def add_gratitude(text: str, date: Optional[str] = None) -> dict:
    """Add a gratitude entry."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    data = load_day(date)
    entry = {
        "id": generate_id(),
        "text": text,
        "createdAt": datetime.now().isoformat()
    }
    data["entries"].append(entry)
    save_day(data)
    git_commit(f"gratitude: {text[:50]}")
    return entry


def list_gratitude(date: Optional[str] = None, days: int = 1) -> list[dict]:
    """List gratitude entries for a date range."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    all_entries = []
    start = datetime.strptime(date, "%Y-%m-%d")

    for i in range(days):
        d = start - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        data = load_day(date_str)
        for entry in data["entries"]:
            entry["date"] = date_str
            all_entries.append(entry)

    return all_entries


def get_streak() -> int:
    """Calculate current gratitude streak (consecutive days with entries)."""
    today = datetime.now()
    streak = 0

    for i in range(365):  # Max 1 year lookback
        d = today - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        data = load_day(date_str)

        if data["entries"]:
            streak += 1
        else:
            # If it's today and empty, that's fine — check yesterday
            if i == 0:
                continue
            break

    return streak


def format_entries(entries: list[dict]) -> str:
    """Format entries for display."""
    if not entries:
        return "No gratitude entries found."

    lines = []
    current_date = None

    for entry in sorted(entries, key=lambda e: e.get("date", ""), reverse=True):
        date = entry.get("date", "")
        if date != current_date:
            if current_date is not None:
                lines.append("")
            lines.append(f"**{date}**")
            current_date = date
        lines.append(f"  - {entry['text']}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Gratitude tracking CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add command
    add_parser = subparsers.add_parser("add", help="Add a gratitude entry")
    add_parser.add_argument("text", help="What you're grateful for")
    add_parser.add_argument("--date", help="Date (YYYY-MM-DD), defaults to today")

    # list command
    list_parser = subparsers.add_parser("list", help="List gratitude entries")
    list_parser.add_argument("--date", help="Start date (YYYY-MM-DD), defaults to today")
    list_parser.add_argument("--days", type=int, default=7, help="Number of days to show")

    # streak command
    subparsers.add_parser("streak", help="Show current gratitude streak")

    args = parser.parse_args()

    if args.command == "add":
        entry = add_gratitude(args.text, args.date)
        print(f"Added: {entry['text']}")
        streak = get_streak()
        if streak > 1:
            print(f"Streak: {streak} days")

    elif args.command == "list":
        entries = list_gratitude(args.date, args.days)
        print(format_entries(entries))

    elif args.command == "streak":
        streak = get_streak()
        if streak == 0:
            print("No streak yet. Start one today!")
        elif streak == 1:
            print("1 day — just getting started")
        else:
            print(f"{streak} days of gratitude")


if __name__ == "__main__":
    main()

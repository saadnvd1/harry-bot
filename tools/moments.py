#!/usr/bin/env python3
"""
Moments CLI — track memorable family moments and wins.
Stores data in vault/moments/ as daily JSON files.

Usage:
    python moments.py add --type family "Baby grabbed my finger and wouldn't let go"
    python moments.py add --type win "Shipped parallel workers for Harry"
    python moments.py list [--type family|win] [--days 30]
    python moments.py random [--type family] [--min-age 7]
    python moments.py search "baby"
"""

import argparse
import json
import os
import random
import string
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Default paths
VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
MOMENTS_PATH = VAULT_PATH / "moments"

VALID_TYPES = ["family", "win"]


def generate_id() -> str:
    """Generate a short random ID."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=7))


def get_file_path(date: str) -> Path:
    """Get the JSON file path for a given date."""
    return MOMENTS_PATH / f"{date}.json"


def load_day(date: str) -> dict:
    """Load moment entries for a specific date."""
    filepath = get_file_path(date)
    if filepath.exists():
        return json.loads(filepath.read_text())
    return {"date": date, "entries": []}


def save_day(data: dict) -> None:
    """Save moment entries for a day."""
    MOMENTS_PATH.mkdir(parents=True, exist_ok=True)
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


def extract_tags(text: str) -> list[str]:
    """Extract tags from text. Auto-detects names and keywords."""
    tags = []
    text_lower = text.lower()

    # Auto-tag family members — customize with your own names
    # if "name" in text_lower:
    #     tags.append("name")

    # Auto-tag milestone moments
    if "first" in text_lower:
        tags.append("first")

    return tags


def add_moment(text: str, moment_type: str, date: Optional[str] = None, extra_tags: Optional[list] = None) -> dict:
    """Add a moment entry."""
    if moment_type not in VALID_TYPES:
        raise ValueError(f"Invalid type: {moment_type}. Must be one of: {VALID_TYPES}")

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    data = load_day(date)
    tags = extract_tags(text)
    if extra_tags:
        tags.extend(extra_tags)
    tags = list(set(tags))  # dedupe

    entry = {
        "id": generate_id(),
        "type": moment_type,
        "text": text,
        "tags": tags,
        "createdAt": datetime.now().isoformat()
    }
    data["entries"].append(entry)
    save_day(data)

    type_label = "moment" if moment_type == "family" else "win"
    git_commit(f"{type_label}: {text[:50]}")
    return entry


def list_moments(moment_type: Optional[str] = None, days: int = 30, date: Optional[str] = None) -> list[dict]:
    """List moment entries for a date range, optionally filtered by type."""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    all_entries = []
    start = datetime.strptime(date, "%Y-%m-%d")

    for i in range(days):
        d = start - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        data = load_day(date_str)
        for entry in data["entries"]:
            if moment_type is None or entry.get("type") == moment_type:
                entry["date"] = date_str
                all_entries.append(entry)

    return all_entries


def random_moment(moment_type: Optional[str] = None, min_age_days: int = 0) -> Optional[dict]:
    """Get a random moment, optionally filtered by type and minimum age."""
    cutoff = datetime.now() - timedelta(days=min_age_days)

    # Scan all JSON files in moments dir
    all_entries = []
    if MOMENTS_PATH.exists():
        for f in MOMENTS_PATH.glob("*.json"):
            try:
                date_str = f.stem
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
                if file_date <= cutoff:
                    data = json.loads(f.read_text())
                    for entry in data.get("entries", []):
                        if moment_type is None or entry.get("type") == moment_type:
                            entry["date"] = date_str
                            all_entries.append(entry)
            except (ValueError, json.JSONDecodeError):
                continue

    if all_entries:
        return random.choice(all_entries)
    return None


def search_moments(query: str, moment_type: Optional[str] = None) -> list[dict]:
    """Search moments by text content."""
    query_lower = query.lower()
    results = []

    if MOMENTS_PATH.exists():
        for f in sorted(MOMENTS_PATH.glob("*.json"), reverse=True):
            try:
                date_str = f.stem
                data = json.loads(f.read_text())
                for entry in data.get("entries", []):
                    if moment_type and entry.get("type") != moment_type:
                        continue
                    if query_lower in entry.get("text", "").lower():
                        entry["date"] = date_str
                        results.append(entry)
            except (ValueError, json.JSONDecodeError):
                continue

    return results


def format_entries(entries: list[dict], show_type: bool = True) -> str:
    """Format entries for display."""
    if not entries:
        return "No moments found."

    lines = []
    current_date = None

    for entry in sorted(entries, key=lambda e: e.get("date", ""), reverse=True):
        date = entry.get("date", "")
        if date != current_date:
            if current_date is not None:
                lines.append("")
            lines.append(f"**{date}**")
            current_date = date

        type_icon = "👨‍👩‍👦" if entry.get("type") == "family" else "🏆"
        type_str = f" {type_icon}" if show_type else ""
        tags_str = ""
        if entry.get("tags"):
            tags_str = f" [{', '.join(entry['tags'])}]"
        lines.append(f"  - {entry['text']}{type_str}{tags_str}")

    return "\n".join(lines)


def get_stats() -> dict:
    """Get moment statistics."""
    family_count = 0
    win_count = 0

    if MOMENTS_PATH.exists():
        for f in MOMENTS_PATH.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                for entry in data.get("entries", []):
                    if entry.get("type") == "family":
                        family_count += 1
                    elif entry.get("type") == "win":
                        win_count += 1
            except (ValueError, json.JSONDecodeError):
                continue

    return {"family": family_count, "wins": win_count, "total": family_count + win_count}


def main():
    parser = argparse.ArgumentParser(description="Moments tracking CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # add command
    add_parser = subparsers.add_parser("add", help="Add a moment")
    add_parser.add_argument("text", help="The moment to record")
    add_parser.add_argument("--type", "-t", required=True, choices=VALID_TYPES, help="Type of moment")
    add_parser.add_argument("--date", help="Date (YYYY-MM-DD), defaults to today")
    add_parser.add_argument("--tags", nargs="+", help="Additional tags")

    # list command
    list_parser = subparsers.add_parser("list", help="List moments")
    list_parser.add_argument("--type", "-t", choices=VALID_TYPES, help="Filter by type")
    list_parser.add_argument("--days", type=int, default=30, help="Number of days to show")
    list_parser.add_argument("--date", help="Start date (YYYY-MM-DD), defaults to today")

    # random command
    random_parser = subparsers.add_parser("random", help="Get a random moment")
    random_parser.add_argument("--type", "-t", choices=VALID_TYPES, help="Filter by type")
    random_parser.add_argument("--min-age", type=int, default=0, help="Minimum age in days")

    # search command
    search_parser = subparsers.add_parser("search", help="Search moments")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--type", "-t", choices=VALID_TYPES, help="Filter by type")

    # stats command
    subparsers.add_parser("stats", help="Show moment statistics")

    args = parser.parse_args()

    if args.command == "add":
        entry = add_moment(args.text, args.type, args.date, args.tags)
        type_label = "Moment" if args.type == "family" else "Win"
        print(f"{type_label} saved: {entry['text']}")
        if entry.get("tags"):
            print(f"Tags: {', '.join(entry['tags'])}")
        stats = get_stats()
        print(f"Total: {stats['family']} moments, {stats['wins']} wins")

    elif args.command == "list":
        entries = list_moments(args.type, args.days, args.date)
        print(format_entries(entries, show_type=(args.type is None)))

    elif args.command == "random":
        entry = random_moment(args.type, args.min_age)
        if entry:
            type_icon = "👨‍👩‍👦" if entry.get("type") == "family" else "🏆"
            print(f"{type_icon} {entry['date']}: {entry['text']}")
        else:
            print("No moments found matching criteria.")

    elif args.command == "search":
        entries = search_moments(args.query, args.type)
        print(format_entries(entries, show_type=(args.type is None)))

    elif args.command == "stats":
        stats = get_stats()
        print(f"Family moments: {stats['family']}")
        print(f"Wins: {stats['wins']}")
        print(f"Total: {stats['total']}")


if __name__ == "__main__":
    main()

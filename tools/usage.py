#!/usr/bin/env python3
"""Check Claude Code usage quota."""

import json
import sys
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def get_token() -> str | None:
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        data = json.loads(CREDENTIALS_FILE.read_text())
        return data.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, OSError):
        return None


def fetch_usage(token: str) -> tuple[dict | None, str | None]:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200] if e.fp else ""
        return None, f"HTTP {e.code}: {body}" if body else f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"URL error: {e.reason}"
    except json.JSONDecodeError as e:
        return None, f"JSON decode: {e}"


def format_reset(iso_str: str | None) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        diff = dt - now
        hours = int(diff.total_seconds() // 3600)
        mins = int((diff.total_seconds() % 3600) // 60)
        if hours > 0:
            return f"resets in {hours}h {mins}m"
        return f"resets in {mins}m"
    except Exception:
        return ""


def main():
    token = get_token()
    if not token:
        print("No Claude credentials found")
        sys.exit(1)

    usage, err = fetch_usage(token)
    if not usage:
        print(f"Failed to fetch usage ({err})")
        sys.exit(1)

    five_hour = usage.get("five_hour", {})
    seven_day = usage.get("seven_day", {})

    five_pct = five_hour.get("utilization", 0)
    seven_pct = seven_day.get("utilization", 0)

    five_reset = format_reset(five_hour.get("resets_at"))
    seven_reset = format_reset(seven_day.get("resets_at"))

    print(f"5-hour: {five_pct:.0f}% {five_reset}")
    print(f"7-day:  {seven_pct:.0f}% {seven_reset}")

    # Exit code based on usage level
    if five_pct >= 90 or seven_pct >= 90:
        sys.exit(2)  # critical
    elif five_pct >= 70 or seven_pct >= 70:
        sys.exit(1)  # warning
    sys.exit(0)


if __name__ == "__main__":
    main()

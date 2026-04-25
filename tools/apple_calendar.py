#!/usr/bin/env python3
"""Calendar operations via apple-bridge API.

Usage:
    python3 tools/apple_calendar.py today                    # List today's events
    python3 tools/apple_calendar.py add "Title" "2026-04-20" "14:00" "15:00"
    python3 tools/apple_calendar.py add "Title" "2026-04-20" "14:00" "15:00" "Work"
"""

import json
import os
import sys
import urllib.request
import urllib.error

BRIDGE_URL = os.environ.get("APPLE_BRIDGE_URL", "http://localhost:3020")
BRIDGE_TOKEN = os.environ.get("APPLE_BRIDGE_TOKEN", "")


def _make_request(url: str, data: bytes = None, method: str = "GET") -> urllib.request.Request:
    headers = {}
    if BRIDGE_TOKEN:
        headers["X-Bridge-Token"] = BRIDGE_TOKEN
    if data:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(url, data=data, headers=headers, method=method)


def get_today() -> dict:
    try:
        req = _make_request(f"{BRIDGE_URL}/calendar/today")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def add_event(title: str, date: str, start_time: str, end_time: str, calendar: str = "") -> dict:
    data = json.dumps({
        "title": title,
        "date": date,
        "startTime": start_time,
        "endTime": end_time,
        "calendar": calendar,
    }).encode()
    req = _make_request(f"{BRIDGE_URL}/calendar/add", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: calendar.py today | add <title> <date> <start> <end> [calendar]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "today":
        result = get_today()
        print(json.dumps(result, indent=2))

    elif cmd == "add":
        if len(sys.argv) < 6:
            print("Usage: calendar.py add <title> <date YYYY-MM-DD> <start HH:MM> <end HH:MM> [calendar]")
            sys.exit(1)
        title = sys.argv[2]
        date = sys.argv[3]
        start = sys.argv[4]
        end = sys.argv[5]
        cal = sys.argv[6] if len(sys.argv) > 6 else ""
        result = add_event(title, date, start, end, cal)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

#!/usr/bin/env python3
"""Create Apple Reminders via the apple-bridge API.

Usage:
    python3 tools/remind.py "Buy groceries" "2026-04-20"
    python3 tools/remind.py "Call dentist" "2026-04-20" "Personal"
    python3 tools/remind.py "Ship feature" ""               # no due date
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


def add_reminder(title: str, due_date: str = "", list_name: str = "") -> dict:
    data = json.dumps({
        "title": title,
        "dueDate": due_date,
        "list": list_name,
    }).encode()
    req = _make_request(f"{BRIDGE_URL}/reminders/add", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        return {"error": f"Bridge unreachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: remind.py <title> [due_date] [list]")
        sys.exit(1)

    title = sys.argv[1]
    due = sys.argv[2] if len(sys.argv) > 2 else ""
    lst = sys.argv[3] if len(sys.argv) > 3 else ""

    result = add_reminder(title, due, lst)
    print(json.dumps(result, indent=2))

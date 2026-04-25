#!/usr/bin/env python3
"""Apple Notes operations via apple-bridge API.

Usage:
    python3 tools/notes.py list                        # List all notes
    python3 tools/notes.py read <note_id>              # Read a note
    python3 tools/notes.py add "Title" "Body text"
    python3 tools/notes.py add "Title" "Body text" "Folder"
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


def list_notes() -> dict:
    try:
        req = _make_request(f"{BRIDGE_URL}/notes/metadata")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def read_note(note_id: str) -> dict:
    try:
        encoded_id = urllib.request.quote(note_id, safe="")
        req = _make_request(f"{BRIDGE_URL}/notes/{encoded_id}/content")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def add_note(title: str, body: str, folder: str = "") -> dict:
    data = json.dumps({
        "title": title,
        "body": body,
        "folder": folder,
    }).encode()
    req = _make_request(f"{BRIDGE_URL}/notes/add", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: notes.py list | read <id> | add <title> <body> [folder]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        result = list_notes()
        print(json.dumps(result, indent=2))

    elif cmd == "read":
        if len(sys.argv) < 3:
            print("Usage: notes.py read <note_id>")
            sys.exit(1)
        note_id = sys.argv[2]
        result = read_note(note_id)
        print(json.dumps(result, indent=2))

    elif cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: notes.py add <title> <body> [folder]")
            sys.exit(1)
        title = sys.argv[2]
        body = sys.argv[3]
        folder = sys.argv[4] if len(sys.argv) > 4 else ""
        result = add_note(title, body, folder)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

#!/usr/bin/env python3
import sys, os
# Prevent tools dir from shadowing stdlib modules
_tools_dir = os.path.dirname(os.path.abspath(__file__))
if _tools_dir in sys.path:
    sys.path.remove(_tools_dir)
"""iMessage operations via apple-bridge API.

Usage:
    python3 tools/messages.py read                     # Read last 20 messages
    python3 tools/messages.py read 50                  # Read last 50 messages
    python3 tools/messages.py send "Jane" "Hello!"     # Lookup contact, then send
    python3 tools/messages.py send "+15551234567" "Hi"  # Verified number (must exist in contacts)
    python3 tools/messages.py lookup "Jane"            # Search contacts
    python3 tools/messages.py threads                  # Recent conversation threads
    python3 tools/messages.py threads 20               # Last 20 threads
    python3 tools/messages.py call "Jane"              # Call a contact (resolved via contacts)
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


def lookup_contact(query: str) -> list:
    """Search contacts by name. Returns list of {name, phone} dicts."""
    try:
        encoded = urllib.request.quote(query)
        req = _make_request(f"{BRIDGE_URL}/contacts?q={encoded}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("contacts", [])
    except Exception as e:
        return []


def _get_all_contacts() -> list:
    """Fetch all contacts (no query filter)."""
    try:
        req = _make_request(f"{BRIDGE_URL}/contacts")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            return data.get("contacts", [])
    except Exception as e:
        return []


def _normalize_phone(phone: str) -> str:
    """Strip all formatting from phone number, keep only digits and leading +."""
    clean = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "").replace(".", "")
    if clean.startswith("+"):
        return "+" + "".join(c for c in clean[1:] if c.isdigit())
    return "".join(c for c in clean if c.isdigit())


def verify_number(phone: str) -> dict | None:
    """Check if a phone number exists in contacts. Returns contact dict or None.

    Fetches all contacts and matches by normalized phone number since the
    contacts API only searches by name, not phone number.
    """
    target = _normalize_phone(phone)
    target_short = target[-10:] if len(target) >= 10 else target

    for c in _get_all_contacts():
        c_phone = _normalize_phone(c.get("phone", ""))
        c_short = c_phone[-10:] if len(c_phone) >= 10 else c_phone
        if c_phone == target or c_short == target_short:
            return c
    return None


def get_threads(limit: int = 10) -> dict:
    """Get recent conversation threads grouped by contact."""
    try:
        req = _make_request(f"{BRIDGE_URL}/threads?limit={limit}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def make_call(recipient: str) -> dict:
    """Call a contact. Recipient resolved via contacts API first."""
    resolved = resolve_recipient(recipient)
    if "error" in resolved:
        return resolved

    phone = resolved["phone"]
    name = resolved["name"]

    data = json.dumps({"phone": phone}).encode()
    req = _make_request(f"{BRIDGE_URL}/call", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            result["calling"] = {"name": name, "phone": phone}
            return result
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}


def read_messages(limit: int = 20) -> dict:
    try:
        req = _make_request(f"{BRIDGE_URL}/messages?limit={limit}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def resolve_recipient(recipient: str) -> dict:
    """Resolve a recipient to a verified phone number.
    
    If recipient is a name → lookup contact, require exactly 1 match.
    If recipient is a phone number → verify it exists in contacts.
    Returns {"phone": "+1...", "name": "..."} or {"error": "..."}.
    """
    # Is it a phone number?
    is_phone = recipient.startswith("+") or recipient.replace("-", "").replace(" ", "").isdigit()
    
    if is_phone:
        contact = verify_number(recipient)
        if contact:
            return {"phone": contact["phone"], "name": contact["name"]}
        else:
            return {"error": f"BLOCKED: Number {recipient} not found in contacts. "
                    f"Will NOT send to unverified numbers. "
                    f"Use 'lookup' to find the correct contact first."}
    else:
        # Name search
        contacts = lookup_contact(recipient)
        if len(contacts) == 0:
            return {"error": f"No contact found matching '{recipient}'. "
                    f"Use 'lookup' to search with different terms."}
        elif len(contacts) == 1:
            return {"phone": contacts[0]["phone"], "name": contacts[0]["name"]}
        else:
            lines = [f"Multiple contacts match '{recipient}':"]
            for c in contacts:
                lines.append(f"  - {c['name']}: {c['phone']}")
            lines.append("Be more specific or use the exact phone number from this list.")
            return {"error": "\n".join(lines)}


def send_message(recipient: str, text: str) -> dict:
    """Send a message. Recipient is verified against contacts before sending."""
    resolved = resolve_recipient(recipient)
    if "error" in resolved:
        return resolved
    
    phone = resolved["phone"]
    name = resolved["name"]
    
    data = json.dumps({
        "recipient": phone,
        "text": text,
    }).encode()
    req = _make_request(f"{BRIDGE_URL}/messages/send", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            result["sent_to"] = {"name": name, "phone": phone}
            return result
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: messages.py read [limit] | send <name-or-phone> <text> | lookup <query>")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "read":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        result = read_messages(limit)
        print(json.dumps(result, indent=2))

    elif cmd == "lookup":
        if len(sys.argv) < 3:
            print("Usage: messages.py lookup <query>")
            sys.exit(1)
        contacts = lookup_contact(sys.argv[2])
        if contacts:
            for c in contacts:
                print(f"  {c['name']}: {c['phone']}")
        else:
            print(f"No contacts matching '{sys.argv[2]}'")

    elif cmd == "send":
        if len(sys.argv) < 4:
            print("Usage: messages.py send <name-or-phone> <text>")
            sys.exit(1)
        recipient = sys.argv[2]
        text = sys.argv[3]
        result = send_message(recipient, text)
        if "error" in result:
            print(f"BLOCKED: {result['error']}", file=sys.stderr)
            sys.exit(1)
        else:
            print(json.dumps(result, indent=2))

    elif cmd == "threads":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        result = get_threads(limit)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        for t in result.get("threads", []):
            name = t.get("display_name") or t.get("handle") or "Unknown"
            last = t.get("messages", [{}])[-1] if t.get("messages") else {}
            last_text = last.get("text", "")[:80] if last else ""
            print(f"  {name}: {last_text}")

    elif cmd == "call":
        if len(sys.argv) < 3:
            print("Usage: messages.py call <name-or-phone>")
            sys.exit(1)
        result = make_call(sys.argv[2])
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

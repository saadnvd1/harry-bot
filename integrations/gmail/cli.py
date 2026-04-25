"""Gmail CLI — IMAP + app password, zero-dep (stdlib only).

Pre-summarized output format for token efficiency when called from Claude:
  list → `[id] sender | subject | date` one line each
  read → full body, but plain-text extracted (no HTML) and truncated

Setup:
  1. Enable 2FA on your Google account
  2. Create an app password: https://myaccount.google.com/apppasswords
  3. Set env vars: GMAIL_USER, GMAIL_APP_PASSWORD (in harry-bot/.env)
"""

from __future__ import annotations

import argparse
import email
import email.utils
import imaplib
import os
import sys
from datetime import date, datetime, timedelta
from email.header import decode_header
from pathlib import Path

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
DEFAULT_LIMIT = 10
BODY_MAX_CHARS = 4000


def _load_env() -> None:
    """Load .env from harry-bot root if present (so CLI works outside worker)."""
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def _connect() -> imaplib.IMAP4_SSL:
    _load_env()
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        print(
            "Error: GMAIL_USER and GMAIL_APP_PASSWORD must be set.\n"
            "Setup: https://myaccount.google.com/apppasswords\n"
            "Then add to .env:\n"
            "  GMAIL_USER=you@gmail.com\n"
            "  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        m = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        m.login(user, pw.replace(" ", ""))
        return m
    except imaplib.IMAP4.error as e:
        print(f"Gmail login failed: {e}", file=sys.stderr)
        sys.exit(3)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for p, enc in parts:
        if isinstance(p, bytes):
            try:
                out.append(p.decode(enc or "utf-8", errors="replace"))
            except LookupError:
                out.append(p.decode("utf-8", errors="replace"))
        else:
            out.append(p)
    return "".join(out).replace("\r", "").replace("\n", " ").strip()


def _fmt_date(raw: str) -> str:
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        delta = now - dt
        if delta.days == 0:
            return dt.strftime("%-I:%M%p")
        if delta.days < 7:
            return dt.strftime("%a %-I:%M%p")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return raw[:20]


def _short_sender(raw: str) -> str:
    name, addr = email.utils.parseaddr(_decode(raw))
    if name:
        return name[:30]
    return addr.split("@")[0][:30]


def _search(m: imaplib.IMAP4_SSL, criteria: str) -> list[bytes]:
    status, data = m.search(None, criteria)
    if status != "OK" or not data or not data[0]:
        return []
    return data[0].split()


def _fetch_headers(m: imaplib.IMAP4_SSL, msg_id: bytes) -> dict:
    status, data = m.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
    if status != "OK" or not data or not data[0]:
        return {}
    raw = data[0][1]
    if not isinstance(raw, (bytes, bytearray)):
        return {}
    hdr = email.message_from_bytes(raw)
    return {
        "from": _short_sender(hdr.get("From", "")),
        "subject": _decode(hdr.get("Subject", "")) or "(no subject)",
        "date": _fmt_date(hdr.get("Date", "")),
    }


def _print_rows(m: imaplib.IMAP4_SSL, ids: list[bytes], limit: int) -> None:
    if not ids:
        print("(no messages)")
        return
    selected = ids[-limit:][::-1]  # newest first
    for mid in selected:
        h = _fetch_headers(m, mid)
        if not h:
            continue
        print(f"[{mid.decode()}] {h['from']:30s} | {h['subject'][:70]:70s} | {h['date']}")


def cmd_list(args) -> None:
    m = _connect()
    try:
        m.select("INBOX", readonly=True)
        if args.today:
            today = date.today().strftime("%d-%b-%Y")
            ids = _search(m, f'(SINCE {today})')
        elif args.unread:
            ids = _search(m, "UNSEEN")
        elif args.from_:
            ids = _search(m, f'(FROM "{args.from_}")')
        else:
            ids = _search(m, "ALL")
        _print_rows(m, ids, args.limit)
    finally:
        m.logout()


def cmd_search(args) -> None:
    q = args.query.strip()
    m = _connect()
    try:
        m.select("INBOX", readonly=True)
        # Translate Gmail-style "from:x subject:y" to IMAP criteria best-effort
        criteria: list[str] = []
        words: list[str] = []
        for tok in q.split():
            if ":" in tok:
                k, _, v = tok.partition(":")
                k = k.lower()
                if k == "from":
                    criteria.append(f'(FROM "{v}")')
                elif k == "subject":
                    criteria.append(f'(SUBJECT "{v}")')
                elif k == "to":
                    criteria.append(f'(TO "{v}")')
                elif k == "newer_than":
                    # e.g. "newer_than:7d"
                    if v.endswith("d"):
                        days = int(v[:-1])
                        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
                        criteria.append(f'(SINCE {since})')
                else:
                    words.append(tok)
            else:
                words.append(tok)
        if words:
            criteria.append(f'(BODY "{" ".join(words)}")')
        if not criteria:
            print("(empty query)")
            return
        ids = _search(m, " ".join(criteria))
        _print_rows(m, ids, args.limit)
    finally:
        m.logout()


def _extract_text(msg: email.message.Message) -> str:
    """Get best-effort plain-text body."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = part.get("Content-Disposition") or ""
            if ctype == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: any text/html, stripped
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    import re
                    return re.sub(r"<[^>]+>", "", html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def cmd_read(args) -> None:
    m = _connect()
    try:
        m.select("INBOX", readonly=True)
        status, data = m.fetch(args.id.encode(), "(RFC822)")
        if status != "OK" or not data or not data[0]:
            print(f"(message {args.id} not found)")
            return
        raw = data[0][1]
        if not isinstance(raw, (bytes, bytearray)):
            print("(unexpected fetch response)")
            return
        msg = email.message_from_bytes(raw)
        print(f"From: {_decode(msg.get('From', ''))}")
        print(f"To: {_decode(msg.get('To', ''))}")
        print(f"Subject: {_decode(msg.get('Subject', ''))}")
        print(f"Date: {msg.get('Date', '')}")
        print("---")
        body = _extract_text(msg).strip()
        if len(body) > BODY_MAX_CHARS:
            body = body[:BODY_MAX_CHARS] + f"\n…(truncated, {len(body) - BODY_MAX_CHARS} more chars)"
        # Collapse excessive blank lines
        import re
        body = re.sub(r"\n{3,}", "\n\n", body)
        print(body)
    finally:
        m.logout()


def cmd_count(args) -> None:
    m = _connect()
    try:
        m.select("INBOX", readonly=True)
        if args.unread:
            ids = _search(m, "UNSEEN")
        elif args.today:
            today = date.today().strftime("%d-%b-%Y")
            ids = _search(m, f'(SINCE {today})')
        else:
            ids = _search(m, "ALL")
        print(len(ids))
    finally:
        m.logout()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="gmail", description="Gmail CLI (read-only IMAP)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="List messages")
    pl.add_argument("--unread", action="store_true")
    pl.add_argument("--today", action="store_true")
    pl.add_argument("--from", dest="from_", default=None, help="Filter by sender substring")
    pl.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    pl.set_defaults(fn=cmd_list)

    ps = sub.add_parser("search", help="Search with Gmail-ish query")
    ps.add_argument("query", help='e.g. "from:jane newer_than:7d", "subject:invoice"')
    ps.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ps.set_defaults(fn=cmd_search)

    pr = sub.add_parser("read", help="Read a specific message by ID")
    pr.add_argument("id")
    pr.set_defaults(fn=cmd_read)

    pc = sub.add_parser("count", help="Count messages matching a filter")
    pc.add_argument("--unread", action="store_true")
    pc.add_argument("--today", action="store_true")
    pc.set_defaults(fn=cmd_count)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

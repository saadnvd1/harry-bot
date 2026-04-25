"""Briefings CLI — read CNN/HN daily briefings. Zero deps, zero LLM.

Briefings are pre-generated markdown files in the configured briefings directory.
This CLI just reads and dumps them — no tokens wasted.

Commands:
  today             Show both CNN and HN briefings for today
  cnn [YYYY-MM-DD]  Show CNN briefing (default: today)
  hn [YYYY-MM-DD]   Show HN briefing (default: today)
  list [N]          List available briefing dates (default: last 7)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

DOCS_DIR = Path(os.environ.get("BRIEFINGS_DIR", "./briefings"))
CNN_DIR = DOCS_DIR / "cnn-briefings"
HN_DIR = DOCS_DIR / "hn-briefings"


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read_briefing(directory: Path, date: str) -> str | None:
    f = directory / f"{date}.md"
    if f.exists():
        return f.read_text()
    return None


def _available_dates(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        (f.stem for f in directory.iterdir() if f.suffix == ".md"),
        reverse=True,
    )


def cmd_today(_args) -> None:
    date = _today()
    cnn = _read_briefing(CNN_DIR, date)
    hn = _read_briefing(HN_DIR, date)

    if not cnn and not hn:
        print(f"No briefings for {date} yet.")
        # Show most recent available
        cnn_dates = _available_dates(CNN_DIR)
        hn_dates = _available_dates(HN_DIR)
        if cnn_dates:
            print(f"Latest CNN: {cnn_dates[0]}")
        if hn_dates:
            print(f"Latest HN: {hn_dates[0]}")
        return

    if cnn:
        print(cnn)
        print("\n---\n")
    else:
        print(f"(no CNN briefing for {date})\n")

    if hn:
        print(hn)
    else:
        print(f"(no HN briefing for {date})")


def cmd_cnn(args) -> None:
    date = args.date or _today()
    content = _read_briefing(CNN_DIR, date)
    if not content:
        dates = _available_dates(CNN_DIR)
        print(f"No CNN briefing for {date}.")
        if dates:
            print(f"Available: {', '.join(dates[:5])}")
        return
    print(content)


def cmd_hn(args) -> None:
    date = args.date or _today()
    content = _read_briefing(HN_DIR, date)
    if not content:
        dates = _available_dates(HN_DIR)
        print(f"No HN briefing for {date}.")
        if dates:
            print(f"Available: {', '.join(dates[:5])}")
        return
    print(content)


def cmd_list(args) -> None:
    n = args.n or 7
    cnn_dates = set(_available_dates(CNN_DIR))
    hn_dates = set(_available_dates(HN_DIR))
    all_dates = sorted(cnn_dates | hn_dates, reverse=True)[:n]

    if not all_dates:
        print(f"No briefings found in {DOCS_DIR}")
        return

    for date in all_dates:
        cnn = "CNN" if date in cnn_dates else "   "
        hn = "HN" if date in hn_dates else "  "
        print(f"{date}  {cnn}  {hn}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="briefings", description="Read CNN/HN daily briefings")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("today", help="Show today's briefings").set_defaults(fn=cmd_today)

    pc = sub.add_parser("cnn", help="Show CNN briefing")
    pc.add_argument("date", nargs="?", help="Date (YYYY-MM-DD), default today")
    pc.set_defaults(fn=cmd_cnn)

    ph = sub.add_parser("hn", help="Show HN briefing")
    ph.add_argument("date", nargs="?", help="Date (YYYY-MM-DD), default today")
    ph.set_defaults(fn=cmd_hn)

    pl = sub.add_parser("list", help="List available briefing dates")
    pl.add_argument("n", nargs="?", type=int, help="Number of dates to show (default 7)")
    pl.set_defaults(fn=cmd_list)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

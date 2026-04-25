"""Learning integration CLI — cheap ops with zero LLM calls.

Commands:
  topics            List all topics with stats (notes, wiki articles, open questions)
  note <topic> <content>  Append a note to today's note file
  baseline <topic>  Dump a topic's baseline.md (for context injection)
  curriculum <topic> Dump a topic's curriculum.md
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

LEARNING_DIR = Path(os.environ.get("LEARNING_DIR", "./learning"))


def _find_topics() -> list[Path]:
    """Return topic dirs (dirs containing baseline.md)."""
    if not LEARNING_DIR.is_dir():
        return []
    return sorted(
        d for d in LEARNING_DIR.iterdir()
        if d.is_dir() and (d / "baseline.md").exists()
    )


def _count_files(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.is_file() and f.suffix == ".md")


def _count_open_questions(topic_dir: Path) -> int:
    qfile = topic_dir / "questions.md"
    if not qfile.exists():
        return 0
    return sum(1 for line in qfile.read_text().splitlines() if line.strip().startswith("- [ ]"))


def _latest_note(topic_dir: Path) -> str | None:
    notes = topic_dir / "notes"
    if not notes.is_dir():
        return None
    files = sorted(f.stem for f in notes.iterdir() if f.suffix == ".md")
    return files[-1] if files else None


def _mentor_name(topic_dir: Path) -> str:
    baseline = topic_dir / "baseline.md"
    for line in baseline.read_text().splitlines():
        if line.strip().startswith("You are **"):
            # Extract "Warren Buffett" from "You are **Warren Buffett** —"
            start = line.index("**") + 2
            end = line.index("**", start)
            return line[start:end]
    return "unknown"


def _curriculum_progress(topic_dir: Path) -> tuple[int, int]:
    """Return (done, total) from curriculum.md checkboxes."""
    cfile = topic_dir / "curriculum.md"
    if not cfile.exists():
        return 0, 0
    done = total = 0
    for line in cfile.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            done += 1
            total += 1
        elif stripped.startswith("- [ ]"):
            total += 1
    return done, total


def cmd_topics(_args) -> None:
    topics = _find_topics()
    if not topics:
        print(f"No topics found in {LEARNING_DIR}")
        return

    for d in topics:
        name = d.name
        mentor = _mentor_name(d)
        notes = _count_files(d / "notes")
        wiki = _count_files(d / "wiki")
        questions = _count_open_questions(d)
        latest = _latest_note(d)
        done, total = _curriculum_progress(d)

        print(f"{name} (mentor: {mentor})")
        progress = f"  progress: {done}/{total} concepts" if total else "  progress: no curriculum yet"
        print(progress)
        print(f"  notes: {notes} days  wiki: {wiki} articles  questions: {questions} open")
        if latest:
            print(f"  last note: {latest}")
        print()


def cmd_note(args) -> None:
    topic_dir = LEARNING_DIR / args.topic
    if not topic_dir.is_dir():
        available = [d.name for d in _find_topics()]
        print(f"Topic '{args.topic}' not found. Available: {', '.join(available) or 'none'}")
        sys.exit(1)

    notes_dir = topic_dir / "notes"
    notes_dir.mkdir(exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")
    note_file = notes_dir / f"{today}.md"

    content = " ".join(args.content)

    if note_file.exists():
        existing = note_file.read_text()
        entry = f"\n## {now}\n\n{content}\n"
        note_file.write_text(existing + entry)
    else:
        note_file.write_text(f"# Notes - {today}\n\n## {now}\n\n{content}\n")

    # Count today's entries
    count = note_file.read_text().count("## ")
    print(f"Noted in {args.topic} ({count} notes today)")


def cmd_baseline(args) -> None:
    topic_dir = LEARNING_DIR / args.topic
    baseline = topic_dir / "baseline.md"
    if not baseline.exists():
        print(f"No baseline for topic '{args.topic}'")
        sys.exit(1)
    print(baseline.read_text())


def cmd_curriculum(args) -> None:
    topic_dir = LEARNING_DIR / args.topic
    cfile = topic_dir / "curriculum.md"
    if not cfile.exists():
        print(f"No curriculum for topic '{args.topic}' yet")
        sys.exit(1)
    print(cfile.read_text())


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="learning", description="Learning knowledge base CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("topics", help="List all topics with stats").set_defaults(fn=cmd_topics)

    pn = sub.add_parser("note", help="Append a note to a topic")
    pn.add_argument("topic", help="Topic name (e.g. investing)")
    pn.add_argument("content", nargs="+", help="Note content")
    pn.set_defaults(fn=cmd_note)

    pb = sub.add_parser("baseline", help="Show a topic's baseline")
    pb.add_argument("topic", help="Topic name")
    pb.set_defaults(fn=cmd_baseline)

    pc = sub.add_parser("curriculum", help="Show a topic's curriculum")
    pc.add_argument("topic", help="Topic name")
    pc.set_defaults(fn=cmd_curriculum)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

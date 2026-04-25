"""Context enrichment for jobs — tiered to save tokens.

Tier system (brain/enrich_tiers.py) controls how much context each message
gets. Trivial messages skip everything. Normal messages get relevant context.
Full messages get the works.

Token budget per tier:
  none:   0 tokens    (ollama/haiku with no context)
  light:  ~50 tokens  (just date)
  normal: ~2-3k       (relevant memories + short history)
  full:   ~5-6k       (vault + memories + full history)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime

from brain.context import build_context, get_profile, search_files, extract_keywords, find_relevant_dirs
from brain.history import format_recent
from brain.memory import get_recent_memories, get_relevant_memories
from brain.enrich_tiers import classify, EnrichTier
from config import Config

logger = logging.getLogger(__name__)
config = Config()

GITHUB_RAW = os.environ.get(
    "VAULT_GITHUB_RAW",
    "",
)



def enrich_chat_prompt(
    user_id: int,
    message: str,
    agent: str = "claude",
    model: str | None = None,
    complexity: str | None = None,
) -> str:
    """Build enriched prompt with tier-based context loading."""
    t0 = time.time()
    tier = classify(message, agent, model, complexity=complexity)
    date = datetime.now().strftime("%Y-%m-%d %A")

    # Light tier — just date and message
    if tier.name in ("none", "light"):
        elapsed = time.time() - t0
        logger.info("enrich [%s]: skip (%.1fs)", tier.name, elapsed)
        return f"[Date: {date}]\n\n{config.OWNER_NAME} says: {message}"

    # Build context based on tier budget
    block = f"[Date: {date}]\n"

    # Vault context (only for full tier, budgeted)
    if tier.vault:
        vault_context = _budgeted_vault_context(message, tier)
        if vault_context:
            context_files = [
                line.split("###")[1].strip()
                for line in vault_context.split("\n")
                if line.startswith("###")
            ]
            block += f"[Vault context:\n{vault_context}\n]\n"
            if context_files:
                file_links = "\n".join(f"  {f} → {GITHUB_RAW}/{f}" for f in context_files)
                block += f"[Raw GitHub links:\n{file_links}\n]\n"

    # Memories (budgeted by tier)
    if tier.memories > 0:
        memories = _budgeted_memories(message, tier)
        if memories and memories != "No memories yet.":
            block += f"[Harry's memories:\n{memories}\n]\n"

    # Conversation history (budgeted by tier)
    if tier.history_turns > 0:
        history = format_recent(user_id, max_turns=tier.history_turns)
        if history and history != "No recent conversation.":
            block += f"[Recent conversation:\n{history}\n]\n"

    elapsed = time.time() - t0
    logger.info("enrich [%s]: %.1fs, ~%d chars", tier.name, elapsed, len(block))

    return f"{block}\n{config.OWNER_NAME} says: {message}"


def _budgeted_vault_context(message: str, tier: EnrichTier) -> str:
    """Load vault context with file count and char budget."""
    keywords = extract_keywords(message)
    if not keywords:
        return ""

    dirs = find_relevant_dirs(keywords)
    if "harry-memory" not in dirs:
        dirs.append("harry-memory")

    files = search_files(config.VAULT_PATH, dirs, keywords, max_files=tier.vault_max_files)
    if not files:
        return ""

    parts = []
    total_chars = 0
    for rel_path, content in files:
        # Per-file truncation
        if len(content) > 2000:
            content = content[:2000] + "\n...(truncated)"
        if total_chars + len(content) > tier.vault_max_chars:
            remaining = tier.vault_max_chars - total_chars
            if remaining > 200:
                content = content[:remaining] + "\n...(truncated)"
            else:
                break
        parts.append(f"### {rel_path}\n{content}")
        total_chars += len(content)

    return "\n\n".join(parts)


def _budgeted_memories(message: str, tier: EnrichTier) -> str:
    """Load memories with relevance matching and char budget."""
    # Try relevance-matched first, fall back to recent
    memories = get_relevant_memories(message, n=tier.memories, max_chars=tier.memory_max_chars)
    if memories and memories != "No memories yet.":
        return memories
    return get_recent_memories(n=tier.memories, max_chars=tier.memory_max_chars)


# --- Proactive job enrichment ---

def _fetch_apple_bridge(endpoint: str, timeout: int = 10) -> dict | None:
    """Fetch data from apple-bridge on Mac."""
    import urllib.request
    import urllib.error
    import json
    url = f"{config.APPLE_BRIDGE_URL}{endpoint}"
    req = urllib.request.Request(url)
    if config.APPLE_BRIDGE_TOKEN:
        req.add_header("X-Bridge-Token", config.APPLE_BRIDGE_TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, Exception):
        return None


def _fetch_weather() -> str:
    """Fetch weather with current temp + high/low via wttr.in JSON."""
    import subprocess
    try:
        result = subprocess.run(
            ["curl", "-s", "wttr.in/Houston,TX?format=j1"],
            capture_output=True, text=True, timeout=8
        )
        import json as _json
        data = _json.loads(result.stdout)
        cur = data["current_condition"][0]
        today = data["weather"][0]
        desc = cur["weatherDesc"][0]["value"]
        return f"{desc}, {cur['temp_F']}°F (Low {today['mintempF']}°F / High {today['maxtempF']}°F)"
    except Exception:
        return "Could not fetch weather"


def _utc_to_local(iso_str: str) -> str:
    """Convert UTC ISO timestamp to local time string (e.g. '6:00 AM')."""
    from zoneinfo import ZoneInfo
    try:
        # Handle both 2026-04-17T11:00:00Z and 2026-04-17T11:00:00+00:00
        cleaned = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        local = dt.astimezone(ZoneInfo(config.TIMEZONE))
        return local.strftime("%-I:%M %p")
    except Exception:
        return iso_str.split("T")[1][:5] if "T" in iso_str else iso_str


def _fetch_calendar() -> str:
    """Fetch today's calendar events, condensed for Telegram."""
    data = _fetch_apple_bridge("/calendar/today")
    if not data or not data.get("events"):
        return "No events today (or Mac offline)"

    all_day = []
    activities = []

    for evt in data["events"][:10]:
        title = evt["title"]
        if evt.get("allDay"):
            all_day.append(title)
        else:
            time_str = _utc_to_local(evt["start"])
            activities.append(f"{time_str} — {title}")

    lines = []
    if all_day:
        lines.append(", ".join(all_day))
    for a in activities:
        lines.append(a)

    return "\n".join(lines)


def _fetch_reminders() -> str:
    """Fetch reminders — only overdue + due today/tomorrow. Rest saved for weekly review."""
    data = _fetch_apple_bridge("/reminders")
    if not data or not data.get("reminders"):
        return "No reminders (or Mac offline)"

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + __import__('datetime').timedelta(days=1)).strftime("%Y-%m-%d")

    recent_overdue = []  # 1-3 days
    stale_overdue = []   # 4+ days
    soon = []

    for rem in data["reminders"]:
        title = rem["title"]
        due = rem.get("dueDate")
        if not due:
            continue
        due_date = due.split("T")[0]
        if due_date < today:
            days_late = (datetime.now() - datetime.fromisoformat(due_date)).days
            if days_late <= 3:
                recent_overdue.append(f"  • {title}")
            else:
                stale_overdue.append(f"  • {title} ({days_late}d)")
        elif due_date <= tomorrow:
            label = "today" if due_date == today else "tomorrow"
            soon.append(f"  • {title} ({label})")

    lines = []
    if recent_overdue:
        lines.append(f"Clear today ({len(recent_overdue)}):")
        lines.extend(recent_overdue)
    if stale_overdue:
        if recent_overdue:
            lines.append("")
        lines.append(f"Sitting ({len(stale_overdue)}):")
        lines.extend(stale_overdue)
    if soon:
        if recent_overdue or stale_overdue:
            lines.append("")
        lines.extend(soon)
    if not recent_overdue and not stale_overdue and not soon:
        return "All clear — nothing overdue or due soon"

    return "\n".join(lines)


def _fetch_repo_activity(repo_path: str, days: int = 3) -> str:
    """Fetch recent git activity from a repo (commit messages only, no hashes)."""
    import subprocess
    try:
        subprocess.run(
            ["git", "pull", "--quiet"], cwd=repo_path,
            capture_output=True, timeout=30
        )
        result = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--format=%s", "-10"],
            cwd=repo_path, capture_output=True, text=True, timeout=10
        )
        commits = result.stdout.strip()
        if not commits:
            return "No recent activity"
        return commits
    except Exception:
        return "Could not fetch"


def enrich_briefing_prompt() -> str:
    import subprocess

    date = datetime.now().strftime("%Y-%m-%d %A")

    # Fetch all data sources
    weather = _fetch_weather()
    calendar = _fetch_calendar()
    reminders = _fetch_reminders()
    memories = get_recent_memories(5)

    # Repo activity
    # Track repos via BRIEFING_REPOS env var: comma-separated "label:path" pairs
    repo_specs = os.environ.get("BRIEFING_REPOS", "")
    repo_sections = ""
    for spec in (s.strip() for s in repo_specs.split(",") if s.strip()):
        label, _, path = spec.partition(":")
        if path:
            activity = _fetch_repo_activity(path)
            repo_sections += f"[{label} recent activity:\n{activity}\n]\n"

    # Services status
    try:
        services = subprocess.run(
            ["sm", "list"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or "Could not fetch"
    except Exception:
        services = "Could not fetch"

    # Claude ONLY writes the analysis sections. Python prepends the fixed
    # schedule/reminders/weather block to the response in runner.py via
    # the briefing_prefix mechanism. This guarantees correct formatting
    # because Claude never touches it.

    return (
        f"[Date: {date}]\n"
        f"[Harry's memories:\n{memories}\n]\n"
        f"{repo_sections}"
        f"[Homelab services:\n{services}\n]\n\n"
        f"Write the ANALYSIS portion of {config.OWNER_NAME}'s morning briefing. "
        "The greeting, weather, schedule, and reminders are already handled — do NOT include them.\n\n"
        "Write these sections:\n"
        "1. For each tracked repo — top 3 focus items based on recent activity (bullet points)\n"
        "2. Follow-ups from memories (bullet points, only if actionable)\n"
        "3. Brief motivational close (1-2 lines)\n\n"
        "Keep it tight. 8-12 lines total. No greeting, no weather, no schedule."
    )


def get_briefing_prefix() -> str:
    """Build the fixed top section of the briefing (weather + schedule + reminders).

    Called by runner.py to prepend to Claude's response. Python-built,
    guarantees correct times and formatting.
    """
    weather = _fetch_weather()
    calendar = _fetch_calendar()
    reminders = _fetch_reminders()
    day_name = datetime.now().strftime("%A")

    return (
        f"Morning, {config.OWNER_NAME}. {day_name} — {weather}\n\n"
        f"{calendar}\n\n"
        f"{reminders}\n\n"
    )


def _fetch_random_moment(min_age_days: int = 7) -> str | None:
    """Fetch a random moment from the vault for 'remember when' surfacing."""
    import subprocess
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
    try:
        from moments import random_moment
        moment = random_moment(moment_type="family", min_age_days=min_age_days)
        if moment:
            return f"{moment['date']}: {moment['text']}"
    except Exception:
        pass
    return None


def enrich_checkin_prompt() -> str:
    memories = get_recent_memories(3)
    date = datetime.now().strftime("%Y-%m-%d %A")

    # Try to surface an old family moment
    remember_when = _fetch_random_moment(min_age_days=7)
    remember_section = ""
    if remember_when:
        remember_section = f"[Remember when: {remember_when}]\n"

    return (
        f"[Date: {date}]\n"
        f"[Harry's memories:\n{memories}\n]\n"
        f"{remember_section}\n"
        f"Send {config.OWNER_NAME} an evening check-in. Ask how their day went. "
        "Reference something specific from their recent memories if possible. "
        "If there's a 'remember when' moment, you can optionally bring it up naturally "
        "(e.g., 'Remember when [moment]? That was [X days/weeks] ago.'). "
        "Keep it warm and brief — 2-3 lines. Don't be pushy."
    )

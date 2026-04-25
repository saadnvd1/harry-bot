"""Learning engine — domain rotation, SRS scanner, curriculum, prompt assembly.

All Python, no LLM calls. Computes SRS state from filesystem (takeaway file
dates), no mutable JSON to corrupt.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)

# --- Domain rotation (interleaving schedule) ---

DOMAIN_SCHEDULE: dict[int, str] = {
    0: "career",       # Monday
    1: "health",       # Tuesday
    2: "coding",       # Wednesday
    3: "finance",      # Thursday
    4: "parenting",    # Friday
    5: "coding",       # Saturday (second coding day — systems/depth)
    6: "marriage",     # Sunday
}

# SRS review intervals (days after learning)
REVIEW_INTERVALS = (1, 3, 7, 14, 30)

# --- Default curricula (seeded on first use) ---

DEFAULT_CURRICULA: dict[str, list[str]] = {
    "coding": [
        "Event loop internals (Node.js / Python asyncio)",
        "Database indexing strategies and query optimization",
        "Memory management and leak debugging",
        "TCP/IP fundamentals — what happens when you curl a URL",
        "Connection pooling and resource management",
        "Concurrency models — threads vs async vs multiprocessing",
        "HTTP/2 and HTTP/3 — what changed and why",
        "DNS resolution and caching layers",
        "SQLite internals — WAL mode, page cache, locking",
        "Linux process model — fork, exec, signals",
        "File I/O — buffering, fsync, write durability guarantees",
        "TLS handshake — what happens in those milliseconds",
        "Git internals — objects, refs, packfiles",
        "Container fundamentals — namespaces, cgroups, overlay fs",
        "System design — load balancing strategies",
    ],
    "marriage": [
        "Active listening — reflecting vs fixing",
        "Gottman's four horsemen and antidotes",
        "Bids for connection — turning toward vs away",
        "Conflict repair — the 5:1 positive-to-negative ratio",
        "Love languages in practice — daily micro-gestures",
        "Managing in-law dynamics without triangulation",
        "Financial alignment — joint goals without losing autonomy",
        "Stress spillover — keeping work frustration out of home",
        "Cultural expectations in marriage — navigating two worlds",
        "Maintaining friendship — the foundation under romance",
    ],
    "career": [
        "Managing up — communicating with leadership effectively",
        "Technical leadership without authority",
        "System design interview patterns",
        "Giving feedback that lands — SBI model",
        "Negotiation fundamentals — anchoring, BATNA, framing",
        "Building influence as an IC (individual contributor)",
        "Time management — Eisenhower matrix in practice",
        "Decision-making frameworks — reversible vs irreversible",
        "Building a professional network authentically",
        "When to stay vs when to leave a role",
    ],
    "health": [
        "Sleep architecture — stages, cycles, optimization",
        "Protein timing and muscle protein synthesis",
        "Progressive overload principles for strength training",
        "Injury prevention — mobility vs flexibility",
        "Stress physiology — cortisol, HPA axis, recovery",
        "Hydration science — electrolytes, absorption, timing",
        "Gut microbiome basics — what actually matters",
        "Caffeine — half-life, tolerance, strategic use",
        "Zone 2 cardio — why low intensity has outsized benefits",
        "Posture and desk ergonomics for developers",
    ],
    "finance": [
        "Tax-advantaged accounts — 401k, IRA, HSA strategy",
        "Index fund investing — why most active managers lose",
        "Home buying — what the monthly payment really includes",
        "Emergency fund sizing — how much is enough",
        "Insurance fundamentals — what you actually need",
        "Compound interest — the math that changes behavior",
        "Debt payoff strategies — avalanche vs snowball",
        "Estate planning basics — wills, beneficiaries, POA",
        "Real estate as investment — rent vs buy math",
        "Tax loss harvesting and capital gains management",
    ],
    "parenting": [
        "Infant sleep regression — 4-month brain reorganization",
        "Attachment theory — secure base and safe haven",
        "Baby development milestones — what actually matters",
        "Patience strategies — managing your own dysregulation",
        "Division of labor — equitable vs equal parenting",
        "Screen time research — what the evidence actually says",
        "Bilingual parenting — raising multilingual kids",
        "Infant feeding — breastfeeding, formula, introducing solids",
        "Baby-proofing priorities — real risks vs anxiety",
        "Self-care as a new parent — oxygen mask principle",
    ],
}


def pick_domain(day_of_week: int | None = None) -> str:
    """Return today's learning domain based on interleaving schedule."""
    if day_of_week is None:
        day_of_week = date.today().weekday()
    return DOMAIN_SCHEDULE[day_of_week]


def get_due_reviews(vault_path: Path) -> list[dict]:
    """Scan takeaway files, return ones due for review today.

    SRS is stateless — computed from filename dates. No mutable state.
    Review at days: 1, 3, 7, 14, 30 after learning.
    """
    takeaways_dir = vault_path / "learning" / "takeaways"
    if not takeaways_dir.exists():
        return []

    today = date.today()
    due = []

    for f in sorted(takeaways_dir.glob("*.md")):
        # Filename format: 2026-04-18-domain-topic-slug.md
        match = re.match(r"(\d{4}-\d{2}-\d{2})-(.+?)-(.*)", f.stem)
        if not match:
            continue

        try:
            learned_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue

        domain = match.group(2)
        topic_slug = match.group(3).replace("-", " ")
        days_since = (today - learned_date).days

        if days_since in REVIEW_INTERVALS:
            # Read first few lines for the key insight
            content = f.read_text(errors="replace")
            # Extract Key Insight line if present
            insight = ""
            for line in content.splitlines():
                if line.startswith("**Key Insight:**"):
                    insight = line.replace("**Key Insight:**", "").strip()
                    break

            due.append({
                "file": f.name,
                "domain": domain,
                "topic": topic_slug,
                "insight": insight,
                "content": content[:500],  # cap for token budget
                "days_since": days_since,
            })

    return due


def get_curriculum_progress(vault_path: Path, domain: str) -> dict:
    """Load curriculum for domain. Returns {covered: list, queue: list}.

    Creates default curriculum file if missing.
    """
    curriculum_dir = vault_path / "learning" / "curriculum"
    curriculum_dir.mkdir(parents=True, exist_ok=True)

    curriculum_file = curriculum_dir / f"{domain}.md"
    if not curriculum_file.exists():
        _seed_curriculum(vault_path, domain)

    text = curriculum_file.read_text(errors="replace")
    covered = []
    queue = []
    section = None

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Covered"):
            section = "covered"
        elif stripped.startswith("## Queue"):
            section = "queue"
        elif stripped.startswith("- ") and section:
            item = stripped[2:].strip()
            if item:
                if section == "covered":
                    covered.append(item)
                else:
                    queue.append(item)

    return {"covered": covered, "queue": queue}


def _seed_curriculum(vault_path: Path, domain: str) -> None:
    """Write default curriculum file for a domain."""
    topics = DEFAULT_CURRICULA.get(domain, [])
    curriculum_dir = vault_path / "learning" / "curriculum"
    curriculum_dir.mkdir(parents=True, exist_ok=True)

    lines = [f"# {domain.title()} — Learning Curriculum", "", "## Covered", ""]
    lines.append("## Queue")
    for topic in topics:
        lines.append(f"- {topic}")
    lines.append("")

    (curriculum_dir / f"{domain}.md").write_text("\n".join(lines))
    logger.info("seeded curriculum for %s (%d topics)", domain, len(topics))


def build_learn_prompt(vault_path: Path, topic: str | None = None) -> tuple[str, str | None]:
    """Assemble full learning prompt with domain, reviews, curriculum.

    Returns (prompt, model_override).
    If topic contains a domain name, uses that domain. Otherwise uses schedule.
    """
    # Load prompt template
    template_path = Path(__file__).parent.parent / "context" / "learn.md"
    template = template_path.read_text()

    # Determine domain
    domain = pick_domain()
    if topic:
        # Check if topic IS a domain name
        topic_lower = topic.lower().strip()
        if topic_lower in DEFAULT_CURRICULA:
            domain = topic_lower
            topic = None  # let curriculum pick the topic
        # Otherwise keep both — user wants specific topic in today's domain

    # Ensure dirs exist
    (vault_path / "learning" / "takeaways").mkdir(parents=True, exist_ok=True)

    # Get SRS reviews due
    reviews = get_due_reviews(vault_path)

    # Get curriculum progress
    progress = get_curriculum_progress(vault_path, domain)
    next_topic = topic or (progress["queue"][0] if progress["queue"] else None)
    covered = progress["covered"]

    # Build review section
    review_section = ""
    if reviews:
        review_items = []
        for r in reviews[:2]:  # max 2 review items to keep session short
            review_items.append(
                f"- **{r['topic']}** ({r['domain']}, learned {r['days_since']}d ago): {r['insight']}"
            )
        review_section = "## Review Items Due\n" + "\n".join(review_items)

    # Build covered section (last 5 to show progression)
    covered_section = ""
    if covered:
        recent = covered[-5:]
        covered_section = "## Recently Covered in " + domain.title() + "\n" + "\n".join(f"- {c}" for c in recent)

    # Detect first session for this domain (no covered topics yet)
    is_first_session = len(covered) == 0

    # Assemble prompt
    prompt = template.format(
        domain=domain.title(),
        domain_lower=domain,
        topic=next_topic or f"freestyle — pick something useful for {domain}",
        review_section=review_section,
        covered_section=covered_section,
        vault_path=vault_path,
        today=date.today().isoformat(),
        is_first_session=str(is_first_session).lower(),
    )

    model = "claude-sonnet-4-6"  # reasoning but not opus-tier
    return prompt, model

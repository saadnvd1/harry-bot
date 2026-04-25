"""Smart context retrieval from the vault. Keyword-based with caching."""

import os
import re
import time
from pathlib import Path
from config import Config

# Keywords → vault directories mapping
TOPIC_MAP = {
    "work": ["work"],
    "business": ["business"],
    "project": ["projects"],
    "code": ["projects"],
    "dev": ["projects"],
    "deploy": ["projects"],
    "feel": ["journal", "harry-memory"],
    "mood": ["journal", "harry-memory"],
    "emotion": ["journal", "harry-memory"],
    "journal": ["journal"],
    "note": ["journal/notes"],
    "workout": ["about"],
    "eat": ["about"],
    "health": ["about"],
}

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "i", "me",
    "my", "you", "your", "he", "she", "it", "we", "they", "what",
    "how", "when", "where", "why", "who", "which", "that", "this",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "about", "into", "through", "and", "but", "or", "not", "so",
    "if", "then", "just", "also", "very", "really", "up", "out",
}

# --- Caches ---
_profile_cache: str | None = None
_profile_loaded_at: float = 0
PROFILE_TTL = 300  # 5 min

_search_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, result)
SEARCH_TTL = 120  # 2 min
MAX_CACHE_ENTRIES = 50


def extract_keywords(message: str) -> list[str]:
    words = re.findall(r'\w+', message.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


def find_relevant_dirs(keywords: list[str]) -> list[str]:
    dirs = set()
    for kw in keywords:
        for topic, topic_dirs in TOPIC_MAP.items():
            if topic in kw or kw in topic:
                dirs.update(topic_dirs)
    if not dirs:
        dirs = {"about", "harry-memory"}
    return list(dirs)


def search_files(vault_path: Path, directories: list[str], keywords: list[str], max_files: int = 5, max_file_chars: int = 0) -> list[tuple[str, str]]:
    scored = []
    for dir_name in directories:
        dir_path = vault_path / dir_name
        if not dir_path.exists():
            continue
        for root, _, files in os.walk(dir_path):
            for fname in files:
                if not fname.endswith(".md"):
                    continue
                fpath = Path(root) / fname
                try:
                    content = fpath.read_text(encoding="utf-8")
                except Exception:
                    continue
                score = 0
                fname_lower = fname.lower()
                content_lower = content.lower()
                for kw in keywords:
                    if kw in fname_lower:
                        score += 3
                    score += content_lower.count(kw)
                if score > 0:
                    rel = str(fpath.relative_to(vault_path))
                    scored.append((score, rel, content))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for _, rel, content in scored[:max_files]:
        if max_file_chars and len(content) > max_file_chars:
            content = content[:max_file_chars] + "\n...(truncated)"
        results.append((rel, content))
    return results


def get_profile(vault_path: Path) -> str:
    """Cached profile read — refreshes every 5 min."""
    global _profile_cache, _profile_loaded_at
    now = time.time()
    if _profile_cache and (now - _profile_loaded_at) < PROFILE_TTL:
        return _profile_cache
    profile_path = vault_path / "about" / "profile.md"
    if profile_path.exists():
        _profile_cache = profile_path.read_text(encoding="utf-8")
    else:
        _profile_cache = "No profile found."
    _profile_loaded_at = now
    return _profile_cache


def build_context(message: str, vault_path: Path | None = None) -> str:
    """Build relevant context with search caching."""
    global _search_cache
    if vault_path is None:
        vault_path = Config.VAULT_PATH

    keywords = extract_keywords(message)
    if not keywords:
        return ""

    # Cache key from sorted keywords
    cache_key = "|".join(sorted(keywords))
    now = time.time()
    if cache_key in _search_cache:
        cached_time, cached_result = _search_cache[cache_key]
        if (now - cached_time) < SEARCH_TTL:
            return cached_result

    # Evict old entries
    if len(_search_cache) > MAX_CACHE_ENTRIES:
        cutoff = now - SEARCH_TTL
        _search_cache = {k: v for k, v in _search_cache.items() if v[0] > cutoff}

    dirs = find_relevant_dirs(keywords)
    if "harry-memory" not in dirs:
        dirs.append("harry-memory")

    files = search_files(vault_path, dirs, keywords)

    if not files:
        _search_cache[cache_key] = (now, "")
        return ""

    parts = []
    total_chars = 0
    max_chars = Config.MAX_CONTEXT_CHARS

    for rel_path, content in files:
        if total_chars + len(content) > max_chars:
            remaining = max_chars - total_chars
            if remaining > 200:
                content = content[:remaining] + "\n...(truncated)"
            else:
                break
        parts.append(f"### {rel_path}\n{content}")
        total_chars += len(content)

    result = "\n\n".join(parts)
    _search_cache[cache_key] = (now, result)
    return result

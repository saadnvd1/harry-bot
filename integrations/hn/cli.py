"""Hacker News CLI — fetch posts, comments, and linked articles. Zero deps (stdlib only).

Output is pre-formatted for token efficiency when called from Claude:
  post  → title, url, score, author, text, then top comments with reply counts
  article → extracted readable text from a URL (HTML stripped, truncated)

Uses the official HN Firebase API: https://hacker-news.firebaseio.com/v0/
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HN_API = "https://hacker-news.firebaseio.com/v0"
ARTICLE_MAX_CHARS = 6000
COMMENT_MAX_DEPTH = 2  # top-level + 1 reply deep
MAX_COMMENTS = 10
USER_AGENT = "harry-bot/1.0 (HN reader integration)"


def _fetch_json(url: str, timeout: int = 10) -> dict | list | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"(fetch failed: {url} — {e})", file=sys.stderr)
        return None


def _fetch_text(url: str, timeout: int = 15) -> str | None:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except Exception as e:
        print(f"(fetch failed: {url} — {e})", file=sys.stderr)
        return None


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities. Good enough for readable extraction."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove nav, header, footer, aside
    text = re.sub(r"<(nav|header|footer|aside)[^>]*>.*?</\1>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace block elements with newlines
    text = re.sub(r"<(br|p|div|h[1-6]|li|tr|blockquote)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_item_id(arg: str) -> int | None:
    """Parse an HN item ID from a URL or raw number."""
    # Direct ID
    if arg.isdigit():
        return int(arg)
    # URL patterns: news.ycombinator.com/item?id=12345
    m = re.search(r"(?:news\.ycombinator\.com/item\?id=|hn\.algolia\.com/.*?)(\d+)", arg)
    if m:
        return int(m.group(1))
    return None


def _fetch_item(item_id: int) -> dict | None:
    return _fetch_json(f"{HN_API}/item/{item_id}.json")


def _fetch_comments_tree(kid_ids: list[int], max_top: int = MAX_COMMENTS, max_depth: int = COMMENT_MAX_DEPTH) -> list[dict]:
    """Fetch top-level comments and one level of replies. Returns flat list with indent info."""
    if not kid_ids:
        return []

    # Fetch top-level comments in parallel
    top_comments = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_fetch_item, kid): kid for kid in kid_ids[:max_top * 2]}
        for f in as_completed(futures):
            item = f.result()
            if item and item.get("type") == "comment" and not item.get("deleted") and not item.get("dead"):
                top_comments.append(item)

    # Sort by number of kids (most-replied-to first)
    top_comments.sort(key=lambda c: len(c.get("kids", [])), reverse=True)
    top_comments = top_comments[:max_top]

    results = []
    reply_ids_to_fetch = []

    for c in top_comments:
        reply_count = len(c.get("kids", []))
        text = _strip_html(c.get("text", ""))
        results.append({
            "id": c["id"],
            "by": c.get("by", "[deleted]"),
            "text": text[:2000],
            "reply_count": reply_count,
            "depth": 0,
        })
        # Queue top 2 replies for fetching
        if max_depth > 1:
            for kid in c.get("kids", [])[:2]:
                reply_ids_to_fetch.append((kid, c["id"]))

    # Fetch replies in parallel
    if reply_ids_to_fetch:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_item, rid): (rid, parent_id) for rid, parent_id in reply_ids_to_fetch}
            reply_map: dict[int, list[dict]] = {}
            for f in as_completed(futures):
                rid, parent_id = futures[f]
                item = f.result()
                if item and item.get("type") == "comment" and not item.get("deleted") and not item.get("dead"):
                    text = _strip_html(item.get("text", ""))
                    reply = {
                        "id": item["id"],
                        "by": item.get("by", "[deleted]"),
                        "text": text[:1000],
                        "reply_count": len(item.get("kids", [])),
                        "depth": 1,
                        "parent_id": parent_id,
                    }
                    reply_map.setdefault(parent_id, []).append(reply)

        # Interleave replies after their parents
        final = []
        for r in results:
            final.append(r)
            for reply in reply_map.get(r["id"], []):
                final.append(reply)
        results = final

    return results


def cmd_post(args) -> None:
    item_id = _extract_item_id(args.target)
    if not item_id:
        print(f"Error: can't parse HN item ID from '{args.target}'")
        sys.exit(1)

    item = _fetch_item(item_id)
    if not item:
        print(f"Error: couldn't fetch item {item_id}")
        sys.exit(1)

    # Print post metadata
    print(f"TITLE: {item.get('title', '(untitled)')}")
    print(f"URL: {item.get('url', '(text post)')}")
    print(f"SCORE: {item.get('score', 0)} | BY: {item.get('by', '?')} | COMMENTS: {item.get('descendants', 0)}")

    # Post text (for Ask HN, Show HN text posts)
    post_text = item.get("text")
    if post_text:
        print(f"\nPOST TEXT:\n{_strip_html(post_text)[:3000]}")

    # Fetch comments
    kids = item.get("kids", [])
    if not kids:
        print("\n(no comments yet)")
        return

    print(f"\n--- TOP COMMENTS (sorted by reply count) ---\n")
    comments = _fetch_comments_tree(kids)

    for c in comments:
        indent = "  " if c["depth"] > 0 else ""
        prefix = "  ^" if c["depth"] > 0 else f"[{c['reply_count']} replies]"
        print(f"{indent}{prefix} {c['by']}:")
        # Indent text for replies
        text_lines = c["text"].split("\n")
        for line in text_lines:
            print(f"{indent}  {line}")
        print()


def cmd_article(args) -> None:
    url = args.url
    raw = _fetch_text(url)
    if not raw:
        print(f"Error: couldn't fetch {url}")
        sys.exit(1)

    text = _strip_html(raw)
    if not text:
        print("(no readable text extracted)")
        return

    if len(text) > ARTICLE_MAX_CHARS:
        text = text[:ARTICLE_MAX_CHARS] + f"\n\n...(truncated, {len(text) - ARTICLE_MAX_CHARS} more chars)"

    print(f"SOURCE: {url}")
    print(f"LENGTH: ~{len(text)} chars")
    print(f"\n---\n\n{text}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="hn", description="Hacker News CLI (read-only)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("post", help="Fetch HN post + top comments")
    pp.add_argument("target", help="HN item ID or URL (e.g. 12345 or https://news.ycombinator.com/item?id=12345)")
    pp.set_defaults(fn=cmd_post)

    pa = sub.add_parser("article", help="Fetch and extract readable text from a URL")
    pa.add_argument("url", help="URL to fetch")
    pa.set_defaults(fn=cmd_article)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()

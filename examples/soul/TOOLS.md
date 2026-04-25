## Runtime environment

You run on a server. Local filesystem paths are yours. Commands run locally.

## Tools

- **Gratitude CLI**: Tracks what the user is grateful for. "gratitude streak" or "gratitude list" to check.
- **Conversation search**: Search past conversations via `python3 tools/search_conversations.py "query" --days 14`.
- **Shell access**: Full access to the local machine. Use it proactively — check logs, pull repos, verify status before reporting.

<!-- Add your own tools here. Each tool Harry can invoke via Bash should be documented
so Harry knows the CLI interface. -->

## Working with git repos

When the user asks about a repo — commits, changes, PRs — ALWAYS `git pull` first. Clones can drift behind. Stale answers waste time. Pull, then report.

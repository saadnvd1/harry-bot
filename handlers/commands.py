"""Telegram command handlers."""

import logging
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from channels.telegram import _escape_html
from config import Config
from brain.memory import save_memory, delete_memory, get_recent_memories
from worker import queue
from worker.scheduler import parse_schedule, next_fire_time, describe_cron

logger = logging.getLogger(__name__)
config = Config()


def auth(func):
    """Decorator to restrict commands to allowed user."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != config.ALLOWED_USER_ID:
            await update.message.reply_text("Nah fam, you're not authorized.")
            return
        return await func(update, context)
    return wrapper


@auth
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! Harry here, your personal assistant.\n\n"
        "Just talk to me naturally, or use commands:\n"
        "/help — see all commands\n"
        "/briefing — morning briefing\n"
        "/remember <thing> — save to my memory\n"
        "/forget <thing> — remove from memory\n"
        "/memory — see my recent memories\n"
        "/run <cmd> — run command on homelab\n"
        "/mac <cmd> — run command on your Mac\n"
        "/sync — sync Apple Notes to vault\n"
        "/projects — dev project status"
    )


@auth
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "<b>Commands:</b>\n"
        "/briefing — morning briefing\n"
        "/remember &lt;thing&gt; — save something to memory\n"
        "/forget &lt;thing&gt; — forget something\n"
        "/memory — see recent memories\n"
        "/run &lt;cmd&gt; — run command on homelab VM\n"
        "/mac &lt;cmd&gt; — run command on Mac via SSH\n"
        "/sync — sync Apple Notes → vault\n"
        "/projects — dev project overview\n"
        "/status — homelab service status\n"
        "/reset — clear my brain, start fresh\n\n"
        "<b>Prefixes:</b>\n"
        "<code>!private</code> — ephemeral session, not recorded to history or memory\n"
        "<code>!bg</code> — queue behind current work, don't interrupt\n"
        "<code>!h !s !opus !claude !codex !opencode !ollama</code> — agent/model overrides\n\n"
        "Or just talk to me naturally about anything.",
        parse_mode="HTML",
    )


@auth
async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/remember", "", 1).strip()
    if not text:
        await update.message.reply_text("Remember what? Usage: /remember <thing>")
        return

    # Use first few words as topic
    topic = " ".join(text.split()[:4])
    path = save_memory(topic, text)
    await update.message.reply_text(f"Got it, saved to memory.")


@auth
async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("/forget", "", 1).strip()
    if not text:
        await update.message.reply_text("Forget what? Usage: /forget <search term>")
        return

    result = delete_memory(text)
    await update.message.reply_text(result)


@auth
async def memory_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memories = get_recent_memories(10)
    await update.message.reply_text(f"<b>Recent memories:</b>\n\n{_escape_html(memories)}", parse_mode="HTML")


@auth
async def run_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a command on the homelab VM (localhost)."""
    cmd = update.message.text.replace("/run", "", 1).strip()
    if not cmd:
        await update.message.reply_text("Usage: /run <command>")
        return

    await update.message.reply_text(f"Running: <code>{_escape_html(cmd)}</code>", parse_mode="HTML")
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout or result.stderr or "(no output)"
        # Telegram message limit
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await update.message.reply_text(f"<pre><code>{_escape_html(output)}</code></pre>", parse_mode="HTML")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("Command timed out (30s limit).")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


@auth
async def mac_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a command on the Mac via SSH over Tailscale."""
    cmd = update.message.text.replace("/mac", "", 1).strip()
    if not cmd:
        await update.message.reply_text("Usage: /mac <command>")
        return

    await update.message.reply_text(f"Running on Mac: <code>{_escape_html(cmd)}</code>", parse_mode="HTML")
    try:
        result = subprocess.run(
            ["ssh", f"{config.MAC_USER}@{config.MAC_IP}", cmd],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout or result.stderr or "(no output)"
        if len(output) > 4000:
            output = output[:4000] + "\n...(truncated)"
        await update.message.reply_text(f"<pre><code>{_escape_html(output)}</code></pre>", parse_mode="HTML")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("Command timed out (30s limit).")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


@auth
async def sync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger Apple Notes sync from Mac → vault."""
    await update.message.reply_text("Syncing Apple Notes...")
    try:
        script = str(config.VAULT_PATH.parent / "harry-bot" / "scripts" / "sync-notes.sh")
        result = subprocess.run(
            ["bash", script], capture_output=True, text=True, timeout=120,
        )
        output = result.stdout or result.stderr or "Sync complete."
        await update.message.reply_text(output[:4000])
    except Exception as e:
        await update.message.reply_text(f"Sync error: {e}")


@auth
async def projects_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show dev project overview from vault."""
    index = config.projects_path / "index.md"
    if index.exists():
        content = index.read_text(encoding="utf-8")
        if len(content) > 4000:
            content = content[:4000] + "\n...(truncated)"
        from channels.telegram import markdown_to_html
        await update.message.reply_text(markdown_to_html(content), parse_mode="HTML")
    else:
        await update.message.reply_text("No project index found. Run /sync first.")


@auth
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show homelab service status via serviceman."""
    try:
        result = subprocess.run(
            ["sm", "list"], capture_output=True, text=True, timeout=10,
        )
        output = result.stdout or "(no services)"
        await update.message.reply_text(f"<pre><code>{_escape_html(output)}</code></pre>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# --- Scheduled tasks ---------------------------------------------------------

@auth
async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a scheduled task. Usage:
    /schedule <when> | <prompt>
    Examples:
      /schedule daily 9am | summarize my unread email
      /schedule weekdays 8am | morning briefing + check gmail
      /schedule every 2 hours | check my homelab and tell me anything weird
      /schedule 0 18 * * * | evening journaling prompt
    """
    raw = update.message.text.replace("/schedule", "", 1).strip()
    if not raw or "|" not in raw:
        await update.message.reply_text(
            "Usage: <code>/schedule &lt;when&gt; | &lt;prompt&gt;</code>\n"
            "Examples:\n"
            "  <code>/schedule daily 9am | summarize my email</code>\n"
            "  <code>/schedule every 30 min | check gratitude streak</code>\n"
            "  <code>/schedule weekdays 8am | morning brief</code>",
            parse_mode="HTML",
        )
        return

    when_raw, _, prompt = raw.partition("|")
    when_raw = when_raw.strip()
    prompt = prompt.strip()
    if not prompt:
        await update.message.reply_text("prompt was empty.")
        return

    try:
        cron = parse_schedule(when_raw)
    except ValueError as e:
        await update.message.reply_text(f"couldn't parse <code>{_escape_html(when_raw)}</code>: {e}", parse_mode="HTML")
        return

    next_run = next_fire_time(cron)
    task_id = queue.add_scheduled_task(
        user_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        cron=cron,
        prompt=prompt,
        next_run=next_run,
        label=when_raw,
    )
    next_three = describe_cron(cron)
    await update.message.reply_text(
        f"Scheduled task #{task_id} — <code>{_escape_html(cron)}</code>\nNext: {next_three}",
        parse_mode="HTML",
    )


@auth
async def schedules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List scheduled tasks for this user."""
    tasks = queue.list_scheduled_tasks(update.effective_user.id)
    if not tasks:
        await update.message.reply_text("No scheduled tasks. Use /schedule to create one.")
        return

    tz = ZoneInfo(config.TIMEZONE)
    lines = ["<b>Scheduled tasks:</b>"]
    for t in tasks:
        nxt = datetime.fromtimestamp(t["next_run"], tz=tz).strftime("%a %b %-d %-I:%M%p")
        status = "" if t["enabled"] else " (disabled)"
        lines.append(
            f"<code>#{t['id']}</code>{status} <code>{_escape_html(t['cron'])}</code> — {_escape_html(t['prompt'][:80])}\n  next: {nxt}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")


@auth
async def unschedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a scheduled task by ID. Usage: /unschedule <id>"""
    raw = update.message.text.replace("/unschedule", "", 1).strip()
    if not raw.isdigit():
        await update.message.reply_text("Usage: <code>/unschedule &lt;id&gt;</code> (see /schedules)", parse_mode="HTML")
        return
    task_id = int(raw)
    if queue.delete_scheduled_task(task_id, update.effective_user.id):
        await update.message.reply_text(f"Deleted task #{task_id}.")
    else:
        await update.message.reply_text(f"No task #{task_id} found.")


@auth
async def integrations_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List available integrations and their status."""
    from integrations import load_integrations
    ints = load_integrations(force=True)
    if not ints:
        await update.message.reply_text("No integrations registered.")
        return
    lines = ["<b>Integrations:</b>"]
    for i in ints:
        status = "✅ active" if i.env_ok else f"⚠️ missing env: {', '.join(i.missing_env)}"
        lines.append(f"• <code>{_escape_html(i.name)}</code> ({i.type}) — {status}")
        if i.description:
            lines.append(f"  {_escape_html(i.description)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@auth
async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset Harry's session — clears conversation context, starts fresh."""
    user_id = update.effective_user.id
    # Reset all agent sessions for this user
    for agent in ("claude", "codex", "opencode", "ollama"):
        queue.reset_session(user_id, agent)
    await update.message.reply_text("Brain cleared. Fresh session — what's up?")


@auth
async def agents_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List registered agent adapters + health check each CLI."""
    import shutil
    import agents.claude   # noqa: F401
    import agents.ollama   # noqa: F401
    import agents.codex    # noqa: F401
    import agents.opencode  # noqa: F401
    from agents.base import _REGISTRY

    lines = ["<b>Agents registered:</b>", ""]
    for name in sorted(_REGISTRY.keys()):
        try:
            adapter = _REGISTRY[name]()
            caps = ", ".join(sorted(adapter.capabilities)) or "—"
        except Exception as e:
            caps = f"(build failed: {e})"

        # Health-check: does the CLI exist / is the daemon up?
        if name == "claude":
            ok = shutil.which("claude") is not None
            status = "✅" if ok else "❌ not installed"
        elif name == "codex":
            ok = shutil.which("codex") is not None
            status = "✅" if ok else "❌ not installed"
        elif name == "opencode":
            ok = shutil.which("opencode") is not None
            status = "✅" if ok else "❌ not installed"
        elif name == "ollama":
            try:
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2).read()
                status = "✅ daemon up"
            except Exception:
                status = "❌ daemon down"
        else:
            status = "?"
        lines.append(f"• <code>{_escape_html(name)}</code> — {status}")
        lines.append(f"  caps: {caps}")

    lines.append("")
    lines.append("<b>Prefixes:</b> <code>!h</code> <code>!s</code> <code>!opus</code> <code>!claude</code> <code>!codex</code> <code>!opencode</code> <code>!ollama</code>")
    lines.append("Or: <code>!&lt;agent&gt;:&lt;model&gt; &lt;msg&gt;</code>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

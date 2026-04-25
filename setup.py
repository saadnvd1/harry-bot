#!/usr/bin/env python3
"""Interactive setup wizard for Harry Bot."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ── Auto-find Python 3.11–3.13 ─────────────────────────────────────────────
# PTB 21.x uses asyncio.get_event_loop() which Python 3.14 removed.
# Cap at 3.13 until PTB ships a fix.

PYTHON_MIN = (3, 11)
PYTHON_MAX = (3, 13)

_need_reexec = (
    not os.environ.get("_HARRY_SETUP_NO_REEXEC")
    and (sys.version_info < PYTHON_MIN or sys.version_info[:2] > PYTHON_MAX)
)

if _need_reexec:
    # Prefer highest compatible version
    for candidate in ("python3.13", "python3.12", "python3.11"):
        found = shutil.which(candidate)
        if found:
            os.environ["_HARRY_SETUP_NO_REEXEC"] = "1"
            os.execvp(found, [found] + sys.argv)
    # Check pyenv
    pyenv = shutil.which("pyenv")
    if pyenv:
        result = subprocess.run(
            [pyenv, "versions", "--bare"], capture_output=True, text=True
        )
        for line in sorted(result.stdout.strip().splitlines(), reverse=True):
            ver = line.strip()
            parts = ver.split(".")
            if len(parts) >= 2:
                minor = int(parts[1])
                if PYTHON_MIN[1] <= minor <= PYTHON_MAX[1]:
                    pyenv_root = subprocess.run(
                        [pyenv, "root"], capture_output=True, text=True
                    ).stdout.strip()
                    pybin = os.path.join(pyenv_root, "versions", ver, "bin", "python3")
                    if os.path.isfile(pybin):
                        os.environ["_HARRY_SETUP_NO_REEXEC"] = "1"
                        os.execvp(pybin, [pybin] + sys.argv)
                    break
    v = sys.version_info
    print(f"\033[38;5;204m✗\033[0m Python {v.major}.{v.minor} detected — need 3.11–3.13")
    print(f"  Install via: brew install python@3.13  or  pyenv install 3.13")
    sys.exit(1)

# ── Colors ──────────────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
BLUE = "\033[38;5;75m"
GREEN = "\033[38;5;114m"
YELLOW = "\033[38;5;221m"
RED = "\033[38;5;204m"
CYAN = "\033[38;5;117m"
GRAY = "\033[38;5;245m"

BOT_DIR = Path(__file__).parent


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print(f"""
{GRAY}          {YELLOW}⚡{GRAY}
       ▄▄▄▄▄▄▄▄▄
      ▐░░▀░░░▀░░▌
      ▐░{YELLOW}(●){GRAY}░{YELLOW}(●){GRAY}░▌
      ▐░░░▄▄▄░░░▌
       ▀▄░░░░░▄▀
     ▄▄██▀▀▀▀▀██▄▄
    █▌            ▐█
    █▌  ▄██████▄  ▐█
    ▀█▄▀        ▀▄█▀{RESET}

{BLUE}{BOLD}    Harry Bot Setup{RESET}
    {GRAY}Named after Harry Potter{RESET}
""")


def step(n, total, title):
    bar = f"{GREEN}{'━' * n}{GRAY}{'━' * (total - n)}{RESET}"
    print(f"  {bar}  {BOLD}Step {n}/{total}{RESET} — {title}\n")


def success(msg):
    print(f"  {GREEN}✓{RESET} {msg}")


def warn(msg):
    print(f"  {YELLOW}!{RESET} {msg}")


def fail(msg):
    print(f"  {RED}✗{RESET} {msg}")


def info(msg):
    print(f"  {GRAY}{msg}{RESET}")


def ask(prompt, default="", secret=False, required=True):
    suffix = f" {DIM}({default}){RESET}" if default else ""
    suffix += f" {DIM}[required]{RESET}" if required and not default else ""
    while True:
        try:
            if secret:
                import getpass
                val = getpass.getpass(f"  {CYAN}›{RESET} {prompt}{suffix}: ")
            else:
                val = input(f"  {CYAN}›{RESET} {prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        val = val or default
        if val or not required:
            return val
        print(f"  {RED}  This field is required.{RESET}")


def confirm(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    try:
        val = input(f"  {CYAN}›{RESET} {prompt} {DIM}({hint}){RESET}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if not val:
        return default
    return val in ("y", "yes")


def divider():
    print(f"\n  {GRAY}{'─' * 44}{RESET}\n")


# ── Steps ───────────────────────────────────────────────────────────────────

def check_prerequisites():
    clear()
    banner()
    step(1, 6, "Prerequisites")

    ok = True

    # Python version
    v = sys.version_info
    if PYTHON_MIN <= v[:2] <= PYTHON_MAX:
        success(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")
    elif v[:2] > PYTHON_MAX:
        fail(f"Python {v.major}.{v.minor} — too new (max 3.{PYTHON_MAX[1]})")
        info("Install via: brew install python@3.13  or  pyenv install 3.13")
    else:
        fail(f"Python {v.major}.{v.minor} — need 3.11+")
        info("Install via: brew install python@3.13  or  pyenv install 3.13")
        ok = False

    # Claude CLI
    claude = shutil.which("claude")
    if claude:
        success(f"Claude CLI found at {claude}")
    else:
        fail("Claude CLI not found")
        info("Install: https://docs.anthropic.com/en/docs/claude-code")
        ok = False

    # pip
    pip = shutil.which("pip3") or shutil.which("pip")
    if pip:
        success("pip available")
    else:
        warn("pip not found — will try python3 -m pip")

    if not ok:
        print()
        fail("Fix the above before continuing.")
        sys.exit(1)

    print()
    success("All prerequisites met")
    divider()
    input(f"  {DIM}Press Enter to continue...{RESET}")


def setup_environment():
    clear()
    banner()
    step(2, 6, "Python environment")

    venv_path = BOT_DIR / "venv"
    if venv_path.exists():
        success("Virtual environment already exists")
    else:
        info("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_path)], check=True)
        success("Created venv/")

    info("Installing dependencies...")
    pip = str(venv_path / "bin" / "pip")
    result = subprocess.run(
        [pip, "install", "-r", str(BOT_DIR / "requirements.txt"), "-q"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        success("Dependencies installed")
    else:
        fail("pip install failed:")
        print(result.stderr[:500])
        sys.exit(1)

    divider()
    input(f"  {DIM}Press Enter to continue...{RESET}")


def _open_url(url):
    """Open URL in default browser, silently fail if can't."""
    try:
        import webbrowser
        webbrowser.open(url)
        return True
    except Exception:
        return False


def configure_telegram():
    # Step A: Bot token
    clear()
    banner()
    step(3, 6, "Telegram — Create your bot")

    print(f"  {BOLD}Create a bot via @BotFather:{RESET}\n")
    info("1. Open Telegram and search for @BotFather")
    info("2. Send  /newbot")
    info("3. Choose a display name (e.g. \"Harry\")")
    info("4. Choose a username (must end in 'bot', e.g. \"my_harry_bot\")")
    info("5. BotFather replies with a token like  1234567890:ABCdefGHI...")
    print()

    if confirm("Open BotFather in Telegram?", default=True):
        _open_url("https://t.me/BotFather")
        info("Opened t.me/BotFather")
    print()

    token = ask("Paste your bot token", secret=True)
    success("Bot token saved")

    print()
    bot_username = ask("Bot username (the one ending in 'bot')", required=True)
    bot_username = bot_username.lstrip("@")
    success(f"Got it — @{bot_username}")

    # Step B: User ID
    clear()
    banner()
    step(3, 6, "Telegram — Get your user ID")

    print(f"  {BOLD}Get your numeric user ID:{RESET}\n")
    info("1. Open Telegram and search for @userinfobot")
    info("2. Send it any message (e.g. \"hi\")")
    info("3. It replies with your ID like  Id: 123456789")
    print()

    if confirm("Open @userinfobot in Telegram?", default=True):
        _open_url("https://t.me/userinfobot")
        info("Opened t.me/userinfobot")
    print()

    user_id = ask("Paste your user ID")
    success("User ID saved")

    # Step C: Name
    clear()
    banner()
    step(3, 6, "Telegram — Your name")

    print(f"  {BOLD}What should Harry call you?{RESET}\n")
    info("Used in conversation history and prompts.")
    print()

    name = ask("Your name", default="User")

    divider()
    return {"TELEGRAM_TOKEN": token, "TELEGRAM_USER_ID": user_id, "OWNER_NAME": name, "BOT_USERNAME": bot_username}


def configure_vault():
    clear()
    banner()
    step(4, 6, "Vault setup")

    print(f"  {BOLD}The vault is where Harry stores memories and conversations.{RESET}\n")
    info("It's a directory with subdirs for memories, conversations, etc.")
    info("You can use any path — it just needs to be writable.")
    print()

    default_vault = str(BOT_DIR / "vault")
    vault_path = ask("Vault path", default=default_vault)
    vault = Path(vault_path)

    dirs = ["harry-memory", "conversations", "about", "journal"]
    vault.mkdir(parents=True, exist_ok=True)
    for d in dirs:
        (vault / d).mkdir(exist_ok=True)
    success(f"Created vault at {vault}")

    # Build profile interactively
    profile = vault / "about" / "profile.md"
    print()
    print(f"  {BOLD}Tell Harry about yourself.{RESET}\n")
    info("The more context you give, the more personal Harry becomes.")
    info("You can always edit vault/about/profile.md later.")
    print()

    p_role = ask("What do you do? (e.g. \"software engineer\", \"student\")", required=False)
    p_goals = ask("What are you working on right now?", required=False)
    p_style = ask("How should Harry talk to you? (e.g. \"direct\", \"casual\", \"professional\")", required=False)
    p_extra = ask("Anything else Harry should know?", required=False)

    lines = ["# About Me", ""]
    if p_role:
        lines.append(f"**Role:** {p_role}")
    if p_goals:
        lines.append(f"**Current focus:** {p_goals}")
    if p_style:
        lines.append(f"**Communication style:** {p_style}")
    if p_extra:
        lines.append(f"\n{p_extra}")
    if len(lines) == 2:
        lines.append("Tell Harry about yourself here.")

    profile.write_text("\n".join(lines) + "\n")
    success("Saved profile.md")

    divider()
    return {"VAULT_PATH": str(vault)}


def configure_optional():
    clear()
    banner()
    step(5, 6, "Optional integrations")

    config = {}

    print(f"  {BOLD}These are optional — skip any you don't need.{RESET}\n")

    # Apple Bridge — not yet open source
    info("Apple Bridge (macOS messages, calendar, notes) — coming soon")
    info("Configure manually in .env when available")
    print()

    # Gmail
    if confirm("Set up Gmail integration? (read-only IMAP)", default=False):
        gmail_user = ask("Gmail address")
        gmail_pass = ask("App password", secret=True)
        config["GMAIL_USER"] = gmail_user
        config["GMAIL_APP_PASSWORD"] = gmail_pass
        success("Gmail configured")
    else:
        info("Skipped Gmail")

    print()

    # Gemini (free tier)
    if confirm("Set up Gemini? (free model for simple queries)", default=False):
        gemini_key = ask("Gemini API key", secret=True)
        config["GEMINI_API_KEY"] = gemini_key
        success("Gemini configured")
    else:
        info("Skipped Gemini")

    divider()
    return config


def write_config(config):
    clear()
    banner()
    step(6, 6, "Finishing up")

    # Write .env
    env_path = BOT_DIR / ".env"
    lines = []
    lines.append("# Generated by setup.py")
    lines.append("")

    sections = {
        "Telegram": ["TELEGRAM_TOKEN", "TELEGRAM_USER_ID", "OWNER_NAME", "BOT_USERNAME"],
        "Vault": ["VAULT_PATH"],
        "Schedule": ["MORNING_BRIEFING_HOUR", "TIMEZONE"],
        "Apple Bridge": ["MAC_IP", "MAC_USER", "APPLE_BRIDGE_URL", "APPLE_BRIDGE_TOKEN"],
        "Gmail": ["GMAIL_USER", "GMAIL_APP_PASSWORD"],
        "Gemini": ["GEMINI_API_KEY"],
    }

    # Add defaults
    config.setdefault("MORNING_BRIEFING_HOUR", "8")
    config.setdefault("TIMEZONE", "America/Chicago")

    for section, keys in sections.items():
        section_lines = []
        for key in keys:
            if key in config:
                section_lines.append(f"{key}={config[key]}")
        if section_lines:
            lines.append(f"# {section}")
            lines.extend(section_lines)
            lines.append("")

    env_path.write_text("\n".join(lines) + "\n")
    success(f"Wrote {env_path}")

    # Copy soul files if not already present
    soul_dir = BOT_DIR / "soul"
    example_dir = BOT_DIR / "examples" / "soul"
    if example_dir.exists():
        for f in example_dir.glob("*.md"):
            dest = soul_dir / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
        success("Soul files ready in soul/")
    else:
        info("No example soul files found — create your own in soul/")

    # Create data dir
    (BOT_DIR / "data").mkdir(exist_ok=True)

    divider()

    # Final instructions
    bot_username = config.get("BOT_USERNAME", "")
    bot_link = f"https://t.me/{bot_username}" if bot_username else "your bot in Telegram"
    bot_display = f"@{bot_username}" if bot_username else "your bot"

    print(f"""  {BOLD}{GREEN}Setup complete!{RESET}

  {BOLD}To start Harry:{RESET}

  {CYAN}./start.sh{RESET}

  {BOLD}Then message {bot_display} on Telegram:{RESET}

  {CYAN}{bot_link}{RESET}

  {GRAY}Send /start to say hello, or just type a message.
  Ctrl+C in the terminal to stop Harry.{RESET}

  {BOLD}Customize Harry:{RESET}

  {GRAY}Edit soul/SOUL.md to change personality
  Edit soul/USER.md to tell Harry about yourself
  Add skills in skills/*.md
  See examples/ for templates{RESET}

  {BOLD}Need help?{RESET} {GRAY}https://github.com/saadnvd1/harry-bot{RESET}
""")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    try:
        check_prerequisites()
        setup_environment()
        telegram_config = configure_telegram()
        vault_config = configure_vault()
        optional_config = configure_optional()

        all_config = {**telegram_config, **vault_config, **optional_config}
        write_config(all_config)
    except KeyboardInterrupt:
        print(f"\n\n  {YELLOW}Setup cancelled.{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

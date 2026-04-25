import os
from pathlib import Path

# Auto-detected project root (where this file lives)
BOT_HOME = Path(__file__).parent

# Personal content directory — soul, skills, personas, context, data.
# Falls back to BOT_HOME (where example files live).
DATA_DIR = Path(os.environ.get("HARRY_DATA_DIR", str(BOT_HOME)))


class Config:
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    ALLOWED_USER_ID = int(os.environ["TELEGRAM_USER_ID"])
    OWNER_NAME = os.environ.get("OWNER_NAME", "User")

    VAULT_PATH = Path(os.environ.get("VAULT_PATH", "./vault"))
    MAC_IP = os.environ.get("MAC_IP", "")
    MAC_USER = os.environ.get("MAC_USER", "")
    APPLE_BRIDGE_URL = os.environ.get("APPLE_BRIDGE_URL", "")
    APPLE_BRIDGE_TOKEN = os.environ.get("APPLE_BRIDGE_TOKEN", "")
    # Legacy alias
    NOTES_BRIDGE_URL = APPLE_BRIDGE_URL

    MORNING_BRIEFING_HOUR = int(os.environ.get("MORNING_BRIEFING_HOUR", "8"))
    TIMEZONE = os.environ.get("TIMEZONE", "America/Chicago")

    # Conversation history kept in memory
    MAX_HISTORY_MESSAGES = 20

    # Max chars of vault context to include per message
    MAX_CONTEXT_CHARS = 16000

    @property
    def about_path(self): return self.VAULT_PATH / "about"
    @property
    def journal_path(self): return self.VAULT_PATH / "journal"
    @property
    def projects_path(self): return self.VAULT_PATH / "projects"
    @property
    def business_path(self): return self.VAULT_PATH / "business"
    @property
    def work_path(self): return self.VAULT_PATH / "work"
    @property
    def memory_path(self): return self.VAULT_PATH / "harry-memory"
    @property
    def conversations_path(self): return self.VAULT_PATH / "conversations"
    @property
    def learning_path(self): return self.VAULT_PATH / "learning"

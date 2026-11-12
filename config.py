import os

# ============================================================
# Railway / Environment variables
# ============================================================

# Master bot token (from @BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Your Telegram user ID (only this user can use the constructor)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Maximum code size in bytes (default 2 MB)
MAX_CODE_SIZE = int(os.getenv("MAX_CODE_SIZE", str(2 * 1024 * 1024)))

# Database path (SQLite)
DB_PATH = os.getenv("DB_PATH", "constructor.db")

# Workspace directory for child bots
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "workspaces")

# Auto-restart settings
AUTO_RESTART = os.getenv("AUTO_RESTART", "true").lower() in ("1", "true", "yes")
MAX_RESTART_ATTEMPTS = int(os.getenv("MAX_RESTART_ATTEMPTS", "5"))
RESTART_DELAY = int(os.getenv("RESTART_DELAY", "10"))  # seconds

# AI Code Generator (optional)
# OpenAI-compatible API (OpenAI, xAI Grok, Together, etc.)
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_API_BASE = os.getenv("AI_API_BASE", "https://api.openai.com/v1").rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")

# Check required values
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

if not ADMIN_ID:
    raise ValueError("ADMIN_ID environment variable is required!")

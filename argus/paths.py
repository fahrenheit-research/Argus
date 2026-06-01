"""Filesystem layout — ~/.argus/ — per PRD §6.3.

In this design build, paths are computed but nothing is written unless
`argus setup --write` or `argus config set` is invoked explicitly.

PRD §13.2 SQLite schema is kept here as documentation for the next milestone:

    CREATE TABLE sessions (
      id TEXT PRIMARY KEY, title TEXT, parent_id TEXT,
      provider TEXT NOT NULL, model TEXT NOT NULL,
      created_at TIMESTAMP NOT NULL, last_active_at TIMESTAMP NOT NULL,
      message_count INTEGER DEFAULT 0,
      tokens_in INTEGER DEFAULT 0, tokens_out INTEGER DEFAULT 0
    );
    CREATE TABLE messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id TEXT REFERENCES sessions(id),
      role TEXT NOT NULL, content TEXT NOT NULL,
      tool_call_json TEXT, ts TIMESTAMP NOT NULL, source TEXT
    );
    CREATE TABLE skills_index (
      name TEXT PRIMARY KEY, path TEXT NOT NULL,
      enabled BOOLEAN DEFAULT 1, description TEXT, trigger_kws TEXT
    );
"""

from pathlib import Path

HOME = Path.home() / ".argus"
ENV_FILE = HOME / ".env"
CONFIG_FILE = HOME / "config.yaml"
DB_FILE = HOME / "argus.db"
USER_MD = HOME / "USER.md"
MEMORY_MD = HOME / "MEMORY.md"
SKILLS_DIR = HOME / "skills"
SESSIONS_DIR = HOME / "sessions"
GATEWAY_DIR = HOME / "gateway"
TELEGRAM_PID = GATEWAY_DIR / "telegram.pid"
LOGS_DIR = HOME / "logs"
UPLOADS_DIR = HOME / "uploads"


def ensure_dirs() -> None:
    """Create the ~/.argus/ layout. Only called by --write paths."""
    for d in (HOME, SKILLS_DIR, SESSIONS_DIR, GATEWAY_DIR, LOGS_DIR, UPLOADS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    HOME.chmod(0o700)

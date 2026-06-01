"""Auto-start background services whenever ARGUS launches.

Called once at the top of every `argus` invocation (root callback and
chat_cmd). Runs in milliseconds because all checks are local (PID probe
+ file existence). Never blocks the CLI; starts new processes detached.

Services managed
────────────────
  telegram   — long-polling gateway if TELEGRAM_BOT_TOKEN + allowed users
  mcp        — each server in ~/.argus/mcp_config.json

Design rules
────────────
  • Idempotent — if already running, just note it and continue.
  • Invisible on success — one compact line per service, only if something
    changed (first start or restart after crash).
  • Never crashes the CLI — every exception is caught and logged as a dim
    hint, not an error panel.
  • start_new_session=True — services survive terminal close.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from argus import paths
from argus.theme import CYAN, DIM, GOLD, MAGENTA, ERR

log = logging.getLogger("argus.autostart")

# ── PID helpers ───────────────────────────────────────────────────────────────


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _read_pid(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text().strip()) if pid_file.exists() else None
    except (ValueError, OSError):
        return None


def _write_pid(pid_file: Path, pid: int) -> None:
    paths.ensure_dirs()
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(pid))


# ── Start helpers ─────────────────────────────────────────────────────────────


def _launch(cmd: list[str], log_file: Path) -> int | None:
    """Launch a detached background process. Returns PID or None on failure."""
    try:
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as lf:
            proc = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ},
            )
        return proc.pid
    except Exception as e:  # noqa: BLE001
        log.debug("launch failed for %s: %s", cmd[0], e)
        return None


# ── Telegram gateway ──────────────────────────────────────────────────────────


_TG_PID_FILE = paths.HOME / "gateway" / "telegram.pid"
_TG_LOG_FILE = paths.HOME / "logs" / "argus-gateway.log"


def _telegram_configured() -> bool:
    """True if both bot token and at least one allowed user are present."""
    from argus import config as _cfg
    cfg = _cfg.load()
    token = _cfg.resolve_secret("TELEGRAM_BOT_TOKEN", cfg)
    users = _cfg.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
    has_users = any(s.strip().isdigit() for s in users.split(","))
    return bool(token) and has_users


def _ensure_telegram() -> tuple[str, str]:
    """Ensure the Telegram gateway is running. Returns (status, detail).

    status: "running" | "started" | "failed" | "skipped"
    detail: human-readable message for inline display
    """
    if not _telegram_configured():
        return "skipped", ""

    pid = _read_pid(_TG_PID_FILE)
    if pid and _pid_alive(pid):
        return "running", f"pid {pid}"

    # Dead or never started — launch now
    cmd = [
        sys.executable, "-c",
        (
            "from argus.config import load as _l; "
            "from argus.platforms.telegram import run_long_polling as _r; "
            "_r(_l())"
        ),
    ]
    pid = _launch(cmd, _TG_LOG_FILE)
    if pid:
        _write_pid(_TG_PID_FILE, pid)
        time.sleep(0.3)                  # brief wait so the process starts polling
        if _pid_alive(pid):
            return "started", f"pid {pid}"
        return "failed", "process exited immediately — check ~/.argus/logs/argus-gateway.log"

    return "failed", "could not launch subprocess"


# ── MCP servers ───────────────────────────────────────────────────────────────


_MCP_CONFIG_FILE = paths.HOME / "mcp_config.json"
_MCP_PIDS_DIR    = paths.HOME / "gateway" / "mcp"


def _mcp_servers() -> dict:
    if not _MCP_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(_MCP_CONFIG_FILE.read_text())
        return data.get("mcpServers") or data.get("servers") or {}
    except Exception:
        return {}


def _ensure_mcp_server(name: str, cfg: dict) -> tuple[str, str]:
    """Ensure one MCP server is running. Returns (status, detail)."""
    pid_file = _MCP_PIDS_DIR / f"{name}.pid"
    log_file  = paths.LOGS_DIR / f"argus-mcp-{name}.log"

    pid = _read_pid(pid_file)
    if pid and _pid_alive(pid):
        return "running", f"pid {pid}"

    command = cfg.get("command")
    if not command:
        return "skipped", "no command configured"

    args: list[str] = cfg.get("args") or []
    env_overrides: dict[str, str] = cfg.get("env") or {}
    full_env = {**os.environ, **env_overrides}

    try:
        _MCP_PIDS_DIR.mkdir(parents=True, exist_ok=True)
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as lf:
            proc = subprocess.Popen(
                [command, *args],
                stdout=lf,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=full_env,
            )
        _write_pid(pid_file, proc.pid)
        return "started", f"pid {proc.pid}"
    except FileNotFoundError:
        return "failed", f"command not found: {command} (install it first)"
    except Exception as e:  # noqa: BLE001
        return "failed", str(e)[:60]


def _ensure_mcp() -> list[tuple[str, str, str]]:
    """Ensure all configured MCP servers are running.

    Returns list of (server_name, status, detail).
    """
    servers = _mcp_servers()
    results: list[tuple[str, str, str]] = []
    for name, cfg in servers.items():
        status, detail = _ensure_mcp_server(name, cfg)
        results.append((name, status, detail))
    return results


# ── Public entry — called from cli.py ────────────────────────────────────────


def run_autostart(console=None) -> None:
    """Check and start all configured background services.

    Prints a compact one-line status to `console` for each service that
    was started or failed. Running services are silent (no output).

    Safe to call multiple times — idempotent.
    """
    if console is None:
        return   # safety guard — never crash when console not available

    # ── Telegram ──────────────────────────────────────────────────────
    try:
        status, detail = _ensure_telegram()
        _print_service_line(console, "telegram", status, detail)
    except Exception as e:  # noqa: BLE001
        log.debug("telegram autostart error: %s", e)

    # ── MCP servers ───────────────────────────────────────────────────
    try:
        for name, status, detail in _ensure_mcp():
            _print_service_line(console, f"mcp:{name}", status, detail)
    except Exception as e:  # noqa: BLE001
        log.debug("mcp autostart error: %s", e)


def _print_service_line(console, name: str, status: str, detail: str) -> None:
    """Print a compact one-line service status — only for FAILURES.

    Silent on the happy path (running OR started cleanly). Users only need
    to see autostart output when something needs attention. PID notices
    are written to ~/.argus/logs/argus-gateway.log instead.
    """
    from rich.text import Text

    # Happy path → log only, never print to chat banner.
    if status in ("running", "started"):
        if status == "started":
            log.info("autostart: %s started — %s", name, detail)
        return

    # Failure → user needs to know
    t = Text()
    t.append("  ⟨◇⟩ ", style=f"bold {MAGENTA}")
    t.append(f"{name}", style=f"bold {CYAN}")
    t.append(" — ", style=DIM)

    if status == "failed":
        t.append("failed to start  ", style=ERR)
        if detail:
            t.append(detail, style=DIM)
    elif status == "skipped":
        pass   # truly silent — not configured, nothing to show
    else:
        t.append(status, style=DIM)

    if status not in ("skipped", "running"):
        console.print(t)

"""`argus gateway` — Telegram adapter lifecycle — PRD §12.6."""

from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from argus.screens._common import err_panel, hint, ok_panel, screen_header
from argus.stub.telegram import TAIL_EVENTS
from argus.theme import console as new_console


def status() -> None:
    console = new_console()
    screen_header(console, "Telegram gateway")

    from argus import config as _config
    from argus.platforms.telegram import parse_allow_list

    cfg = _config.load()
    token = _config.resolve_secret("TELEGRAM_BOT_TOKEN", cfg)

    if not token:
        hint(console, "No TELEGRAM_BOT_TOKEN configured.")
        hint(console, "Run:  argus gateway setup")
        return

    # Check if gateway process is running
    pid = _read_pid()
    is_running = pid is not None and _pid_is_running(pid)

    # Validate token to get bot username
    from argus.validate import validate_telegram_token
    ok, detail = validate_telegram_token(token)
    bot_name = detail.split()[0] if ok else "unknown"

    # Get allow-list
    raw = _config.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
    allowed_ids = parse_allow_list(raw)

    body = Text()
    if is_running:
        body.append("✓ ", style="argus.ok")
        body.append("telegram gateway online", style="argus.fg")
        body.append(f"   (pid {pid})\n\n", style="argus.dim")
    else:
        body.append("○ ", style="argus.dim")
        body.append("telegram gateway offline", style="argus.fg")
        body.append("\n\n", style="argus.dim")

    body.append("bot:           ", style="argus.dim")
    body.append(f"{bot_name}" if ok else "token invalid", style="argus.cyan")
    body.append("\n")
    body.append("token valid:   ", style="argus.dim")
    body.append("✓ yes" if ok else "✗ no", style="argus.ok" if ok else "argus.err_text")
    body.append("\n")
    body.append("allowed users: ", style="argus.dim")
    body.append(str(len(allowed_ids)), style="argus.cyan")
    body.append("\n")
    body.append("voice STT:     ", style="argus.dim")
    body.append(f"{cfg.voice.stt_provider} · {cfg.voice.stt_model}", style="argus.cyan")

    console.print(Padding(Panel(body, border_style="argus.gold", padding=(1, 2)), (0, 0)))

    if not is_running:
        hint(console, "Start with:  argus gateway start")


def setup_only() -> None:
    """Re-enter just the Telegram half of the wizard."""
    from argus.screens.setup import _botfather_walkthrough  # type: ignore[attr-defined]
    from argus import picker
    from argus.validate import validate_telegram_token

    console = new_console()
    screen_header(console, "Telegram setup", subtitle="replace token, change allow-list.")

    choice = picker.select(
        "How do you want to proceed?",
        choices=[
            "I have a fresh BotFather token",
            "Walk me through creating a bot",
            "Cancel",
        ],
    )
    if not choice or choice == "Cancel":
        return
    if choice == "Walk me through creating a bot":
        _botfather_walkthrough(console)

    while True:
        token = picker.password("Paste your Telegram bot token:")
        if not token:
            return
        console.print(Text("  [ Validating with Telegram getMe… ]", style="argus.dim"))
        ok, detail = validate_telegram_token(token.strip())
        if ok:
            ok_panel(console, f"Connected as {detail}")
            # Persist the token
            from argus import config as _config
            cfg = _config.load()
            env = dict(cfg.env)
            env["TELEGRAM_BOT_TOKEN"] = token.strip()
            _config.save_env(env)
            cfg.gateway.telegram.enabled = True
            _config.save_config(cfg)
            ok_panel(console, "Token saved to ~/.argus/.env",
                     sub="Start the gateway with:  argus gateway start")
            break
        err_panel(console, f"That token didn't work: {detail}",
                  sub="Re-copy from BotFather and try again.")


def start(as_service: bool = False) -> None:
    console = new_console()
    screen_header(console, "Telegram gateway — start")

    if as_service:
        body = Text()
        body.append("Dry-run preview — these would be written:\n\n", style="argus.dim")
        body.append("  /etc/systemd/system/argus-gateway.service\n", style="argus.cyan")
        body.append("  → systemctl --user enable --now argus-gateway", style="argus.cyan")
        console.print(Padding(Panel(body, border_style="argus.magenta", padding=(1, 2)), (0, 0)))
        return

    # If a real bot token is configured, run the REAL Telegram gateway.
    # Otherwise fall back to the canned tail so the design is still
    # explorable in demo mode.
    from argus import config as _config
    cfg = _config.load()
    token = _config.resolve_secret("TELEGRAM_BOT_TOKEN", cfg)

    if token:
        try:
            from argus.platforms.telegram import run_long_polling
            ok_panel(
                console,
                "telegram gateway online   (real, long-polling)",
                sub="Listening for messages from your allow-list.\nPress Ctrl-C to stop.",
            )
            run_long_polling(cfg)
        except RuntimeError as e:
            err_panel(console, str(e))
        except KeyboardInterrupt:
            console.print(Text("\n⟨◇⟩ stopped.", style="argus.gold"))
        return

    # Demo fallback.
    ok_panel(
        console,
        f"telegram gateway online   (pid {_FAUX_PID}, demo mode)",
        sub=f"No TELEGRAM_BOT_TOKEN set — showing canned events.\n"
            f"Run `argus setup --write` with a real BotFather token to go live.\n\n"
            f"Press Ctrl-C to stop.",
    )

    console.print()
    console.print(Text("  tailing live events…", style="argus.dim"))
    console.print()
    _stream_tail(console)


def _stream_tail(console: Console) -> None:
    """Stream the canned event tail with realistic pacing."""
    try:
        for offset, level, text in TAIL_EVENTS:
            time.sleep(min(offset, 0.6) if offset > 0 else 0.1)
            line = Text()
            line.append("  ")
            line.append("tg ", style="argus.cyan")
            line.append("⟨◇⟩ ", style="argus.bracket")
            if level == "tool":
                line.append(text, style="argus.tool")
            elif level == "info":
                line.append(text, style="argus.fg")
            else:
                line.append(text, style="argus.dim")
            console.print(line)
        console.print()
        hint(console, "(end of canned tail · in a real run this would keep streaming)")
    except KeyboardInterrupt:
        console.print(Text("\n⟨◇⟩ stopped.", style="argus.gold"))


def _read_pid() -> int | None:
    """Return the PID from ~/.argus/gateway/telegram.pid, or None."""
    from argus import paths
    try:
        if paths.TELEGRAM_PID.exists():
            return int(paths.TELEGRAM_PID.read_text().strip())
    except (ValueError, OSError):
        pass
    return None


def _pid_is_running(pid: int) -> bool:
    import os
    try:
        os.kill(pid, 0)   # signal 0 = probe only, no actual signal
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def stop() -> None:
    console = new_console()
    screen_header(console, "Telegram gateway — stop")
    _stop_inner(console, verbose=True)


def _stop_inner(console, *, verbose: bool = True) -> bool:
    """Production-grade stop: SIGTERM → poll up to 8s → SIGKILL if needed.
    Returns True if we know the process is gone. Used by `restart` so it
    can be sure the new gateway doesn't fight a zombie predecessor."""
    import os, signal, time
    from argus import paths

    pid = _read_pid()
    pids_to_kill: list[int] = [pid] if pid and _pid_is_running(pid) else []

    # Belt-and-braces: also search for ANY stray gateway Python process.
    # (Old PID files get out of sync; cron restarts; users start by hand.)
    pids_to_kill.extend(_find_gateway_processes())
    pids_to_kill = list({p for p in pids_to_kill if p and p != os.getpid()})

    if not pids_to_kill:
        if verbose:
            hint(console, "No gateway process found.")
        paths.TELEGRAM_PID.unlink(missing_ok=True)
        return True

    for p in pids_to_kill:
        try:
            os.kill(p, signal.SIGTERM)
            if verbose:
                hint(console, f"Sent SIGTERM to pid {p}, waiting up to 8s for clean exit…")
        except OSError as e:
            if verbose:
                err_panel(console, f"Could not signal pid {p}: {e}")

    # Poll for clean exit
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if not any(_pid_is_running(p) for p in pids_to_kill):
            paths.TELEGRAM_PID.unlink(missing_ok=True)
            if verbose:
                ok_panel(console, f"Gateway stopped cleanly (pid(s): {', '.join(map(str, pids_to_kill))})")
            return True
        time.sleep(0.25)

    # Still alive → SIGKILL the holdouts
    for p in pids_to_kill:
        if _pid_is_running(p):
            try:
                os.kill(p, signal.SIGKILL)
                if verbose:
                    hint(console, f"pid {p} ignored SIGTERM — sent SIGKILL.")
            except OSError:
                pass
    time.sleep(0.5)
    paths.TELEGRAM_PID.unlink(missing_ok=True)
    if verbose:
        ok_panel(console, "Gateway force-stopped.", sub="Any in-flight messages were dropped.")
    return True


def _find_gateway_processes() -> list[int]:
    """Find stray gateway processes by command-line scan (best-effort)."""
    import subprocess
    try:
        # ps with full command line; portable across macOS + Linux.
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line: continue
        if ("argus.platforms.telegram" in line
                or "run_long_polling" in line
                or "argus.gateway" in line):
            try:
                pids.append(int(line.split(None, 1)[0]))
            except (ValueError, IndexError):
                continue
    return pids


def restart() -> None:
    """Hard restart: kill the running gateway (any pid we can find) and start
    a fresh one with the current code. After this returns, the new gateway
    is verified running."""
    import time
    console = new_console()
    screen_header(console, "Telegram gateway — restart")

    _stop_inner(console, verbose=True)

    # Brief settle so Telegram releases the bot's getUpdates session.
    time.sleep(1.0)

    from argus.screens.setup import _start_gateway_background
    pid, err = _start_gateway_background()
    if not pid:
        err_panel(console, f"Could not start gateway: {err}",
                  sub="Try manually:  argus gateway start")
        return

    # Verify the new process is alive after a moment (catches immediate crashes).
    time.sleep(2.0)
    if not _pid_is_running(pid):
        err_panel(console, f"Gateway pid {pid} crashed within 2s of starting.",
                  sub="Check ~/.argus/logs/argus-gateway.log for the traceback.")
        return

    ok_panel(
        console,
        f"Gateway restarted (pid {pid}) with the latest code.",
        sub=("New code paths now LIVE — TTS routes to Telegram, /commands "
             "menu refreshed, vision auto-finds recent images."),
    )


def allow(user_id: str | None, list_only: bool) -> None:
    """Add a user to the allow-list AND persist to ~/.argus/.env."""
    from argus import config as _config
    from argus.platforms.telegram import parse_allow_list, add_user_to_allow_list

    console = new_console()
    cfg = _config.load()

    if list_only or user_id is None:
        screen_header(console, "Telegram allow-list")
        raw = _config.resolve_secret("TELEGRAM_ALLOWED_USERS", cfg) or ""
        ids = sorted(parse_allow_list(raw))
        if not ids:
            hint(console, "Allow-list is empty — add yourself with:  argus gateway allow <user_id>")
            hint(console, "Find your ID by messaging @userinfobot on Telegram.")
            return
        t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
        t.add_column("Telegram user ID", style="argus.cyan", no_wrap=True)
        for uid in ids:
            t.add_row(str(uid))
        console.print(t)
        return

    if not str(user_id).isdigit():
        err_panel(console, "Allow-list IDs must be numeric. Did you paste a @username?")
        return

    add_user_to_allow_list(str(user_id))
    ok_panel(console, f"added {user_id} to the allow-list.",
             sub="Persisted to ~/.argus/.env — restart the gateway for it to take effect.")


def revoke(user_id: str) -> None:
    """Remove a user from the allow-list AND persist to ~/.argus/.env."""
    from argus import config as _config
    from argus.platforms.telegram import remove_user_from_allow_list

    console = new_console()
    was_there = remove_user_from_allow_list(user_id)
    if not was_there:
        err_panel(console, f"{user_id} is not on the allow-list.",
                  sub="Use `argus gateway allow --list` to see who is.")
        return
    ok_panel(console, f"removed {user_id} from the allow-list.",
             sub="Persisted to ~/.argus/.env — restart the gateway for it to take effect.")


def logs(n: int = 20, grep: str | None = None) -> None:
    console = new_console()
    screen_header(console, "Gateway log tail", subtitle=f"last {n} lines" + (f" · /{grep}/" if grep else ""))
    lines = [
        ("2026-05-30 21:09:01", "info", "long-poll cycle started"),
        ("2026-05-30 21:09:14", "info", "← @aniket: \"daily summary\""),
        ("2026-05-30 21:09:14", "tool", "calling github.list_prs(repo='argus-org/argus', since='24h')…"),
        ("2026-05-30 21:09:16", "tool", "done in 2.1s — 4 PRs found"),
        ("2026-05-30 21:09:16", "info", "stream started (provider=groq, model=llama-3.3-70b-versatile)"),
        ("2026-05-30 21:09:19", "info", "stream ended (1,184 tokens out, ttft 1.3s)"),
        ("2026-05-30 21:09:19", "info", "reaction set: ✓"),
        ("2026-05-30 21:13:44", "warn", "message from non-allowed user 184992011 ignored"),
        ("2026-05-30 21:14:02", "info", "← @aniket: \"summarise PRs\""),
        ("2026-05-30 21:14:05", "info", "stream ended (842 tokens out, ttft 1.1s)"),
    ]
    if grep:
        lines = [l for l in lines if grep.lower() in (l[1] + " " + l[2]).lower()]
    for ts, level, msg in lines[-n:]:
        line = Text()
        line.append(ts + "  ", style="argus.dim")
        style = "argus.tool" if level == "tool" else ("argus.err_text" if level == "warn" else "argus.cyan")
        line.append(f"{level:<5}", style=style)
        line.append("  ")
        line.append(msg, style="argus.fg")
        console.print(line)

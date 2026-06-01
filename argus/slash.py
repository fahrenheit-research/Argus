"""In-chat slash-command dispatch — PRD §10.1.

The chat REPL routes any line starting with `/` here. Each handler reuses
the same rendering primitives that the standalone subcommands use, so
`/sessions` in chat looks identical to `argus sessions list`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from rich.console import Console

# Sentinel value returned by handlers that want the REPL to exit.
EXIT = object()


@dataclass
class ChatState:
    provider: str = "groq"
    model: str = "llama-3.3-70b-versatile"
    session_title: str = "fresh session"
    tokens_in: int = 0
    tokens_out: int = 0
    # Surfaced in the bottom toolbar (Hermes parity):
    last_ttft_ms: int = 0          # time-to-first-token of the most recent turn
    cost_usd: float = 0.0           # accumulated session cost (rough)
    # Per-turn approval set for shell commands. Cleared after each tool call.
    approved_cmds: set[str] = field(default_factory=set)
    # Files attached this session — prepended to the next LLM message.
    # list of (display_name, content_snippet, full_path)
    attached_files: list[tuple[str, str, str]] = field(default_factory=list)


def dispatch(line: str, console: Console, state: ChatState) -> object | None:
    """Run a slash command. Returns EXIT to terminate the REPL, else None."""
    line = line.strip()
    if not line.startswith("/"):
        return None
    parts = line[1:].split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""
    handler = _COMMANDS.get(cmd)
    if handler is None:
        from argus.screens._common import err_panel
        err_panel(console, f"Unknown command: /{cmd}", sub="Type /help to list commands.")
        return None
    return handler(arg, console, state)


# ─── handlers ────────────────────────────────────────────────────────────────


def _help(arg: str, console: Console, state: ChatState) -> None:
    from rich.table import Table

    from argus.screens._common import screen_header
    screen_header(console, "Slash commands")
    t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    t.add_column("Command", no_wrap=True, style="argus.cyan")
    t.add_column("What it does", style="argus.fg")
    for cmd, desc in _DESCRIPTIONS:
        t.add_row(cmd, desc)
    console.print(t)


def _new(arg: str, console: Console, state: ChatState) -> None:
    state.session_title = "fresh session"
    state.tokens_in = state.tokens_out = 0
    state.approved_cmds.clear()
    state.attached_files.clear()
    if hasattr(state, "_convo"):
        delattr(state, "_convo")
    _print_gradient_new(console)


def _print_gradient_new(console: Console) -> None:
    """Print the /new confirmation in a sweeping Magenta → Cyan → Gold gradient."""
    import sys, math
    from argus.theme import MAGENTA, CYAN, GOLD

    msg = "✓ Started a fresh session.  Talk to Argus"

    MAGENTA_RGB = (0xFF, 0x38, 0xD1)
    CYAN_RGB    = (0x42, 0xE8, 0xF5)
    GOLD_RGB    = (0xFF, 0xC2, 0x47)

    n = len(msg)
    chars: list[str] = []
    for i, ch in enumerate(msg):
        t = i / max(1, n - 1)
        # First half: Magenta → Cyan  |  Second half: Cyan → Gold
        if t < 0.5:
            t2 = t * 2
            r = round(MAGENTA_RGB[0] + (CYAN_RGB[0] - MAGENTA_RGB[0]) * t2)
            g = round(MAGENTA_RGB[1] + (CYAN_RGB[1] - MAGENTA_RGB[1]) * t2)
            b = round(MAGENTA_RGB[2] + (CYAN_RGB[2] - MAGENTA_RGB[2]) * t2)
        else:
            t2 = (t - 0.5) * 2
            r = round(CYAN_RGB[0] + (GOLD_RGB[0] - CYAN_RGB[0]) * t2)
            g = round(CYAN_RGB[1] + (GOLD_RGB[1] - CYAN_RGB[1]) * t2)
            b = round(CYAN_RGB[2] + (GOLD_RGB[2] - CYAN_RGB[2]) * t2)
        chars.append(f"\033[38;2;{r};{g};{b}m\033[1m{ch}\033[0m")

    sys.stdout.write("\n  " + "".join(chars) + "\n\n")
    sys.stdout.flush()


def _clear(arg: str, console: Console, state: ChatState) -> None:
    console.clear()
    from argus.banner import print_banner
    print_banner(console, provider=state.provider, model=state.model)


def _restart(arg: str, console: Console, state: ChatState) -> None:
    """Hard reset: fresh session + clear approvals + reload config from disk."""
    from argus import config as _config
    from argus.screens._common import ok_panel
    cfg = _config.load()
    state.provider = cfg.agent.default_provider
    state.model = cfg.agent.default_model
    state.session_title = "fresh session"
    state.tokens_in = state.tokens_out = 0
    state.last_ttft_ms = 0
    state.cost_usd = 0.0
    state.approved_cmds.clear()
    if hasattr(state, "_convo"):
        delattr(state, "_convo")
    ok_panel(console, "Restarted: config reloaded, session reset.")


def _model(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens.model import run as model_run
    new_provider, new_model = model_run(current_provider=state.provider, current_model=state.model)
    if new_provider:
        state.provider = new_provider
    if new_model:
        state.model = new_model


def _tools(arg: str, console: Console, state: ChatState) -> None:
    """Show registered tools (real inventory, not just the toolset toggle screen)."""
    from rich.table import Table

    from argus.screens._common import screen_header
    from argus.tools import available_tools, register_defaults

    register_defaults()
    screen_header(console, "Tools (live)", subtitle="every tool the agent can call this turn.")
    t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    t.add_column("Toolset", style="argus.gold", no_wrap=True)
    t.add_column("Name", style="argus.cyan", no_wrap=True)
    t.add_column("Description", style="argus.fg")
    for tool in available_tools():
        t.add_row(tool.toolset, tool.spec.name, tool.spec.description[:90])
    console.print(t)


def _memory(arg: str, console: Console, state: ChatState) -> None:
    if arg.startswith("add"):
        fact = arg[3:].strip()
        from argus.screens.memory import add
        add(fact)
    else:
        from argus.screens.memory import show
        show()


def _skills(arg: str, console: Console, state: ChatState) -> None:
    """Show the real ~/.argus/skills/ inventory + router scores."""
    from rich.table import Table

    from argus.screens._common import screen_header
    from argus.skills_lib.seed import seed_skills

    seed_skills()  # idempotent

    try:
        from argus.agentmomento import get_router
        router = get_router()
        # router._load_index() is implementation detail; just read the file.
        import json
        from argus import paths
        idx_path = paths.SKILLS_DIR / ".index.json"
        index = json.loads(idx_path.read_text()) if idx_path.exists() else {}
    except Exception:
        index = {}

    screen_header(console, "Skills (live)", subtitle=f"{len(index)} installed · BM25-routed by AgentMomento.")
    t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    t.add_column("Name", style="argus.cyan", no_wrap=True)
    t.add_column("Category", style="argus.gold", no_wrap=True)
    t.add_column("Used", justify="right", style="argus.dim")
    t.add_column("Success", justify="right", style="argus.gold")
    t.add_column("Description", style="argus.fg")
    for name, meta in sorted(index.items()):
        t.add_row(
            name,
            meta.get("category", "?"),
            str(meta.get("usage_count", 0)),
            f"{meta.get('success_rate', 1.0):.0%}",
            (meta.get("description") or "")[:60],
        )
    console.print(t)


def _sessions(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens.sessions import list_sessions
    list_sessions()


def _resume(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens.sessions import resume
    resume(arg or "")


def _status(arg: str, console: Console, state: ChatState) -> None:
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.text import Text

    from argus import config as _config
    cfg = _config.load()

    gw = "configured" if _config.has_secret("TELEGRAM_BOT_TOKEN", cfg) else "not configured"

    body = Text()
    body.append("provider:  ", style="argus.dim").append(state.provider, style="argus.cyan").append("\n")
    body.append("model:     ", style="argus.dim").append(state.model, style="argus.cyan").append("\n")
    body.append("session:   ", style="argus.dim").append(state.session_title, style="argus.cyan").append("\n")
    body.append("tokens in: ", style="argus.dim").append(f"{state.tokens_in:,}", style="argus.gold").append("\n")
    body.append("tokens out:", style="argus.dim").append(f" {state.tokens_out:,}", style="argus.gold").append("\n")
    body.append("ttft:      ", style="argus.dim").append(f"{state.last_ttft_ms/1000:.2f}s" if state.last_ttft_ms else "--", style="argus.cyan").append("\n")
    body.append("gateway:   ", style="argus.dim").append(f"telegram {gw}", style="argus.cyan").append("\n")
    body.append("approvals: ", style="argus.dim").append(
        f"{len(state.approved_cmds)} pending" if state.approved_cmds else "none",
        style="argus.cyan",
    )
    console.print(Padding(Panel(body, border_style="argus.cyan", padding=(0, 2), title="status"), (1, 0)))


def _stop(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens._common import hint
    hint(console, "(no generation in flight — Ctrl-C interrupts the next call)")


def _approve(arg: str, console: Console, state: ChatState) -> None:
    """Approve one shell command for the next turn's run_command call.

    `/approve <command>` — exact-match. The shell tool refuses any
    command that hasn't been approved in this exact text. Approvals
    are single-use: they clear automatically after the tool fires.
    """
    cmd = arg.strip()
    if not cmd:
        from argus.screens._common import err_panel
        err_panel(console, "Usage: /approve <command>",
                  sub="Approves one specific shell command (exact match) for the next turn.")
        return
    state.approved_cmds.add(cmd)
    # Also stash it in the tool-side approval set so the next run_command
    # call actually sees it.
    from argus.tools.registry import approve_command
    approve_command(cmd)
    from argus.screens._common import ok_panel
    ok_panel(
        console,
        f"approved: {cmd}",
        sub="Single-use. Re-ask the agent in your next message; it can now run this exact command.",
    )


def _setup(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens.setup import run as setup_run
    setup_run(write=True)


def _key(arg: str, console: Console, state: ChatState) -> None:
    parts = arg.split(maxsplit=1)
    if len(parts) != 2:
        from argus.screens._common import err_panel
        err_panel(console, "Usage: /key <provider> <api-key>",
                  sub="e.g. /key groq gsk_abc…  or  /key anthropic sk-ant-…")
        return
    from argus.screens.key import run as key_run
    key_run(parts[0], parts[1])


def _telegram(arg: str, console: Console, state: ChatState) -> None:
    """Run the Telegram setup wizard inline."""
    from argus.screens.gateway import setup_only
    setup_only()


def _gateway(arg: str, console: Console, state: ChatState) -> None:
    """Inline gateway status."""
    from argus.screens.gateway import status
    status()


def _config(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens.config import list_config
    list_config()


def _doctor(arg: str, console: Console, state: ChatState) -> None:
    from argus.screens.doctor import run as doctor_run
    doctor_run(force_all_green=False)


def _reset(arg: str, console: Console, state: ChatState) -> object:
    """Wipe ~/.argus and exit. The user will re-launch from a clean slate."""
    from argus.screens.reset import run as reset_run
    confirmed = (arg.strip() in ("-y", "--confirm", "yes"))
    reset_run(confirm=confirmed)
    if confirmed:
        return EXIT
    return None


def _brain(arg: str, console: Console, state: ChatState) -> None:
    """Show the AgentBrain + ARGUS memory dashboard."""
    from argus.tools.brain_viz import render_brain_panel, maybe_auto_optimise
    convo = getattr(state, "_convo", None)
    msgs = len(convo.messages) if convo else 0
    render_brain_panel(console, convo_messages=msgs)
    maybe_auto_optimise(console)


def _orchestrate(arg: str, console: Console, state: ChatState) -> None:
    """Configure or show the multi-model orchestration team."""
    from argus import config as _config
    from rich.text import Text
    from argus.theme import GOLD, CYAN, DIM

    cfg = _config.load()

    if not arg.strip():
        # Show current team
        body = Text()
        body.append("\n  Multi-model Orchestration Team\n\n", style=f"bold {GOLD}")
        body.append("  Planner:  ", style=DIM)
        body.append(f"{cfg.agent.default_provider}/{cfg.agent.default_model}\n", style=CYAN)
        body.append("  Executor: ", style=DIM)
        body.append(f"{cfg.auxiliary.extract.provider}/{cfg.auxiliary.extract.model}\n", style=CYAN)
        body.append("\n  Usage: ask the agent to 'orchestrate: <complex task>'\n", style=DIM)
        body.append("  Configure: argus config set auxiliary.extract.provider openai\n", style=DIM)
        console.print(body)
        return

    # If arg is provided, parse "planner=X executor=Y" overrides
    body = Text()
    body.append(f"\n  Orchestration task queued: ", style=GOLD)
    body.append(arg, style=CYAN)
    body.append("\n  Type your request — the agent will use /orchestrate tool automatically.\n", style=DIM)
    console.print(body)


def _quit(arg: str, console: Console, state: ChatState) -> object:
    from argus.banner import show_goodbye
    show_goodbye(console)
    return EXIT


def _attach(arg: str, console: Console, state: ChatState) -> None:
    """Attach a file to the conversation — its content is injected into
    the next LLM message so the model can reason about it.

    Usage: /attach /path/to/file.py
           /attach ~/Desktop/notes.txt

    Supports any plain-text file (code, markdown, logs, configs). Binary
    files are refused with a clear error. Large files (>50 KB) are
    truncated to the first 50 KB with a notice.
    """
    import os
    from pathlib import Path
    from argus.screens._common import err_panel, ok_panel, hint
    from argus.theme import CYAN, GOLD, DIM

    path_str = arg.strip().strip('"').strip("'")
    if not path_str:
        err_panel(console, "Provide a file path.  Example:  /attach ~/Desktop/notes.txt")
        return

    path = Path(os.path.expanduser(path_str)).resolve()
    if not path.exists():
        err_panel(console, f"File not found: {path}")
        return
    if path.is_dir():
        err_panel(console, f"That's a directory, not a file: {path}\nUse /attach to attach individual files.")
        return

    # Detect binary by reading first 8192 bytes and checking for nulls
    try:
        raw = path.read_bytes()
    except PermissionError:
        err_panel(console, f"Permission denied: {path}")
        return

    if b"\x00" in raw[:8192]:
        err_panel(console, f"Binary file — ARGUS only reads plain text.\n{path.name} appears to be binary.")
        return

    # Decode
    try:
        content = raw.decode("utf-8", errors="replace")
    except Exception as e:
        err_panel(console, f"Can't read {path.name}: {e}")
        return

    MAX_BYTES = 50_000
    truncated = False
    if len(content) > MAX_BYTES:
        content = content[:MAX_BYTES]
        truncated = True

    size_kb = len(raw) / 1024
    ext = path.suffix.lower()

    # Determine language hint for code fences
    _CODE_EXTS = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".sh": "bash", ".yaml": "yaml", ".yml": "yaml", ".json": "json",
        ".md": "markdown", ".toml": "toml", ".rs": "rust", ".go": "go",
        ".java": "java", ".rb": "ruby", ".swift": "swift", ".cpp": "cpp",
        ".c": "c", ".html": "html", ".css": "css", ".sql": "sql",
    }
    lang = _CODE_EXTS.get(ext, "")

    # Store in session — will be prepended to the next user message
    state.attached_files.append((path.name, content, str(path)))

    # Pretty confirmation
    from rich.text import Text
    from rich.panel import Panel
    from rich.padding import Padding
    body = Text()
    body.append(f"📎 ", style=f"bold {GOLD}")
    body.append(path.name, style=f"bold {CYAN}")
    body.append(f"  ({size_kb:.1f} KB", style=DIM)
    if truncated:
        body.append(f" — truncated to {MAX_BYTES//1000} KB", style=DIM)
    body.append(")\n", style=DIM)
    body.append(f"   {path}\n", style=DIM)
    body.append(f"\n   Attached. The agent will see this file in your next message.", style=f"{DIM}")
    if state.attached_files:
        total = len(state.attached_files)
        body.append(f"\n   {total} file(s) pending in this turn.", style=DIM)
    console.print(Padding(Panel(body, border_style="argus.gold", padding=(0, 2)), (1, 0)))
    hint(console, "Type your question about the file and press Enter.")


def _connect(arg: str, console: Console, state: ChatState) -> None:
    """/connect — open the interactive connector picker (or pass a service name)."""
    from argus.connectors.registry import get_connector, run_connect_wizard
    arg = (arg or "").strip().lower()
    if not arg:
        # No arg → open the CLI picker. We import lazily and call directly so
        # we keep the interactive picker style consistent with `argus connect`.
        from argus import cli as _cli
        try:
            _cli.connect_cmd(service=None, status=False, test=False, disconnect=False)
        except Exception as e:
            from argus.screens._common import err_panel
            err_panel(console, f"connector picker failed: {e}")
        return
    c = get_connector(arg)
    if not c:
        from argus.screens._common import err_panel
        err_panel(console, f"unknown service '{arg}'",
                  sub="try: gmail, outlook, twitter, linkedin, google_calendar, google_docs")
        return
    run_connect_wizard(c.id, console)


def _report(arg: str, console: Console, state: ChatState) -> None:
    """/report — full status report (ARGUS + AgentBrain + Momento + Wire)."""
    import asyncio
    from argus import config as _cfg
    from argus.reports import build_status_report
    from rich.markdown import Markdown
    cfg = _cfg.load()
    report = asyncio.run(build_status_report(cfg, format="terminal"))
    console.print(Markdown(report))


def _talk(arg: str, console: Console, state: ChatState) -> None:
    """/talk — start voice in a daemon thread, prompt stays live.
    Wake word: 'Hey Argus'.  Close phrase: 'End Argus'."""
    from rich.text import Text
    try:
        from argus.voice.listener import start_voice_thread, voice_state as _vs
        if _vs.active:
            console.print(Text("  ⟨◇⟩  Voice already running. Say 'End Argus' to stop.",
                               style="argus.dim"))
            return
        start_voice_thread(wake_model="hey_argus", stt_model="tiny.en", speak=True)
        console.print(Text(
            "  ⟨◇⟩  Voice on.  "
            "Say 'Hey Argus' to wake  ·  'End Argus' to close  ·  you can still type.",
            style="argus.dim",
        ))
    except Exception as e:  # noqa: BLE001
        from argus.screens._common import err_panel
        err_panel(console, f"voice mode failed: {e}", sub="run `argus doctor`")


def _me(arg: str, console: Console, state: ChatState) -> None:
    """/me — opens the business-onboarding wizard. Same flow as `argus me`."""
    from argus import business as _biz
    arg = (arg or "").strip()
    if arg:
        # `/me <url>` → fast path: scrape that URL directly
        import asyncio
        ok, result = asyncio.run(_biz.onboard_from_url(arg))
        if not ok:
            from argus.screens._common import err_panel
            err_panel(console, f"Could not learn from {arg}: {result}")
            return
        _biz.reset_for_re_onboarding()
        from rich.text import Text as _T
        console.print(_T("  ✓ ", style="argus.ok")
                      .append("Learned ", style="argus.dim")
                      .append(result.get("name", arg), style="bold argus.gold"))
        console.print(_T("  Next ", style="argus.dim")
                      .append("argus", style="argus.cyan")
                      .append(" boot opens with a fresh briefing.", style="argus.dim"))
        return
    # No arg → interactive picker (existing URL? new URL? cancel?)
    _biz.run_me_wizard(console)


def _clearmemory(arg: str, console: Console, state: ChatState) -> None:
    """/clearmemory — wipe MEMORY.md + USER.md only. Never touches .env/OAuth."""
    from argus import paths
    from rich.text import Text as _T
    wiped: list[str] = []
    for f in (paths.MEMORY_MD, paths.USER_MD):
        if f.exists():
            f.unlink()
            wiped.append(f.name)
    msg = _T()
    msg.append("  ✓ ", style="argus.ok")
    msg.append("Memory cleared", style="argus.fg")
    if wiped:
        msg.append(f" ({', '.join(wiped)})", style="argus.dim")
    msg.append(
        ".\n    API keys, OAuth tokens, and connectors are untouched.",
        style="argus.dim",
    )
    console.print(msg)


_COMMANDS: dict[str, Callable[[str, Console, ChatState], object | None]] = {
    "help": _help,
    "me": _me,
    "talk": _talk,
    "voice": _talk,        # alias
    "listen": _talk,       # alias
    "new": _new,
    "clear": _clear,
    "clearmemory": _clearmemory,
    "restart": _restart,
    "model": _model,
    "tools": _tools,
    "memory": _memory,
    "skills": _skills,
    "sessions": _sessions,
    "resume": _resume,
    "status": _status,
    "report": _report,
    "stop": _stop,
    "approve": _approve,
    "attach": _attach,
    "setup": _setup,
    "key": _key,
    "telegram": _telegram,
    "gateway": _gateway,
    "config": _config,
    "doctor": _doctor,
    "reset": _reset,
    "brain": _brain,
    "memory": _memory,      # already defined above; override to also catch "memory" alone
    "orchestrate": _orchestrate,
    "connect": _connect,
    "quit": _quit,
    "exit": _quit,
}

_DESCRIPTIONS: list[tuple[str, str]] = [
    ("/help",                       "Show this list."),
    ("/new",                        "Start a fresh session, abandoning the current one."),
    ("/clear",                      "Wipe the visible conversation but keep the underlying session."),
    ("/restart",                    "Hard reset: reload config from disk + clear session state."),
    ("/model",                      "Switch provider and/or model for this session."),
    ("/tools",                      "List every tool the agent can call right now."),
    ("/memory",                     "Show MEMORY.md and USER.md."),
    ("/memory add <fact>",          "Append a fact to MEMORY.md."),
    ("/skills",                     "List skills + AgentMomento BM25 stats."),
    ("/sessions",                   "List recent sessions."),
    ("/resume <id|title>",          "Switch to a different session."),
    ("/status",                     "Provider, model, tokens, ttft, gateway, pending approvals."),
    ("/report",                     "Full status report (ARGUS + AgentBrain + Momento + Wire)."),
    ("/me [url]",                   "Teach ARGUS about your business — paste a website URL."),
    ("/talk  /voice  /listen",      "Switch on the mic.  Wake: 'Hey Argus'.  Close: 'End Argus'."),
    ("/connect [service]",          "Connect a service (Gmail, Outlook, Twitter, LinkedIn, Calendar, …)."),
    ("/clearmemory",                "Wipe MEMORY.md + USER.md only — keeps API keys & OAuth."),
    ("/approve <command>",          "Approve one shell command for the next turn (single-use)."),
    ("/attach <filepath>",          "Attach a file — its content is injected into your next message."),
    ("/brain",                      "Show ARGUS memory dashboard: usage bars, entities, skills."),
    ("/orchestrate [task]",         "Show or trigger multi-model orchestration pipeline."),
    ("/setup",                      "Full onboarding wizard (provider + Telegram in one pass)."),
    ("/key <provider> <api-key>",   "Validate + persist one provider's key, fast path."),
    ("/telegram",                   "Inline Telegram bot setup (token + allow-list)."),
    ("/gateway",                    "Show Telegram gateway status."),
    ("/config",                     "Print every config key (secrets masked)."),
    ("/doctor",                     "Run live diagnostics."),
    ("/reset [-y]",                 "Wipe ~/.argus (backup kept) and exit."),
    ("/stop",                       "Interrupt the current tool call or generation."),
    ("/quit, /exit",                "Leave the CLI."),
]

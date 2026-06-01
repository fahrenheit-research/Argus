"""Real-time TUI dashboard — `argus dashboard`.

WHY this module exists:
  When ARGUS is running multiple things at once (Telegram gateway,
  Constellation swarms, background tools), a single-line status bar
  isn't enough. The dashboard gives you a full-screen TUI cockpit:

      ┌─ STATUS ─────────────────────┐  ┌─ ACTIVE AGENTS ──────────────┐
      │ provider: groq · llama-3.3   │  │ ▲ Architect      ✓ done      │
      │ memory:   2.1 KB / 32 KB     │  │ ◐ Scout-1        ⚡ running   │
      │ tools:    40 registered      │  │ ◐ Scout-2        ⚡ running   │
      │ gateway:  ✓ running (pid 47k)│  │ ✚ Medic          ○ idle       │
      └──────────────────────────────┘  └──────────────────────────────┘

      ┌─ TOOL EVENTS ────────────────────────────────────────────────────┐
      │ 23:14:02  ⚡ web_research(query="Mistral pricing")  done 3.2s    │
      │ 23:14:08  ⚡ scribe_pdf("q4_report.pdf")            done 1.1s    │
      │ 23:14:11  ⚡ gmail_send(to="alice@he2.ai")          done 0.6s    │
      └─────────────────────────────────────────────────────────────────┘

  Built with Textual (BSD-licensed, pure Python, runs in any terminal).
  Graceful: if Textual isn't installed, the command prints clear install
  instructions instead of crashing.

  Updates pull from:
    • argus/config       — provider, model, toolsets
    • argus/swarm        — live constellation events (subscribes via a queue)
    • argus/paths        — memory file sizes
    • argus/services     — gateway PID
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ── Event bus for live updates ──────────────────────────────────────────────


@dataclass
class ToolEvent:
    ts:          float
    tool:        str
    args_preview: str
    status:      str        # "running" | "done" | "error"
    elapsed_ms:  int = 0
    detail:      str = ""


# Module-level event bus — anything in ARGUS can post here. The dashboard
# subscribes when it's open; events are dropped silently otherwise so the
# bus has zero cost in non-dashboard runs.
_EVENT_BUS: deque[ToolEvent] = deque(maxlen=200)
_SUBSCRIBERS: list[asyncio.Queue] = []


def publish_tool_event(evt: ToolEvent) -> None:
    """Anyone in the agent loop can call this. Cheap when no dashboard open."""
    _EVENT_BUS.append(evt)
    for q in list(_SUBSCRIBERS):
        try:
            q.put_nowait(evt)
        except asyncio.QueueFull:
            pass


def _subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    _SUBSCRIBERS.append(q)
    # Replay recent history so the dashboard isn't blank on first open
    for e in list(_EVENT_BUS)[-30:]:
        try: q.put_nowait(e)
        except asyncio.QueueFull: break
    return q


def _unsubscribe(q: asyncio.Queue) -> None:
    if q in _SUBSCRIBERS:
        _SUBSCRIBERS.remove(q)


# ── Public entry — called by `argus dashboard` ──────────────────────────────


def run_dashboard() -> None:
    """Launch the TUI. Blocks until user presses q / Ctrl-C."""
    try:
        from textual.app import App
    except ImportError:
        print()
        print("  \x1b[1;38;2;255;92;92m✗\x1b[0m  textual not installed.")
        print("  Run:  \x1b[38;2;66;232;245muv sync --extra dashboard\x1b[0m")
        print("  or:   \x1b[38;2;66;232;245mpip install textual\x1b[0m")
        print()
        return

    app = _build_app()
    app.run()


def _build_app():
    """Lazy-built Textual app so the import only happens when needed."""
    from textual.app import App, ComposeResult
    from textual.containers import Grid, Vertical, Horizontal
    from textual.widgets import Header, Footer, Static, DataTable, RichLog

    # Brand colors as Textual CSS variables
    BRAND_CSS = """
    Screen {
        background: #050505;
    }
    Header {
        background: #FF38D1;
        color: #050505;
    }
    Footer {
        background: #1a1a1a;
        color: #F5E6C8;
    }
    #status_panel {
        border: round #FF38D1;
        padding: 0 1;
        height: 10;
    }
    #agents_panel {
        border: round #42E8F5;
        padding: 0 1;
        height: 10;
    }
    #events_panel {
        border: round #FFC247;
        padding: 0 1;
    }
    #status_panel > .label  { color: #7A7A7A; }
    #status_panel > .value  { color: #F5E6C8; }
    .ok    { color: #FFC247; }
    .err   { color: #FF5C5C; }
    .info  { color: #42E8F5; }
    """

    class ArgusDashboard(App):
        CSS = BRAND_CSS
        TITLE = "⟨◇⟩  ARGUS Dashboard"
        SUB_TITLE = "live cockpit · q to quit"
        BINDINGS = [
            ("q", "quit", "quit"),
            ("r", "refresh", "refresh"),
            ("c", "clear_events", "clear events"),
        ]

        def compose(self) -> ComposeResult:
            yield Header()
            with Grid(id="top"):
                with Vertical(id="status_panel"):
                    yield Static("STATUS", classes="info")
                    yield Static(id="status_body")
                with Vertical(id="agents_panel"):
                    yield Static("ACTIVE AGENTS", classes="info")
                    self.agents_log = RichLog(id="agents_body", wrap=True, highlight=False)
                    yield self.agents_log
            with Vertical(id="events_panel"):
                yield Static("TOOL EVENTS", classes="info")
                self.events_log = RichLog(id="events_body", wrap=False, highlight=False)
                yield self.events_log
            yield Footer()

        def on_mount(self) -> None:
            self.set_interval(2.0, self.action_refresh)
            self.action_refresh()
            self._queue = _subscribe()
            self.run_worker(self._drain_events(), exclusive=False)

        async def _drain_events(self) -> None:
            try:
                while True:
                    evt: ToolEvent = await self._queue.get()
                    self._render_event(evt)
            except asyncio.CancelledError:
                pass
            finally:
                _unsubscribe(self._queue)

        def _render_event(self, evt: ToolEvent) -> None:
            ts = time.strftime("%H:%M:%S", time.localtime(evt.ts))
            color = ("yellow"  if evt.status == "running" else
                     "green"   if evt.status == "done"    else
                     "red")
            line = (f"[dim]{ts}[/]  "
                    f"[bold #FFC247]⚡[/]  "
                    f"[#42E8F5]{evt.tool}[/]"
                    f"[dim]({evt.args_preview})[/]  "
                    f"[{color}]{evt.status}[/]  "
                    f"[dim]{evt.elapsed_ms/1000:.1f}s[/]" if evt.elapsed_ms else
                    f"[dim]{ts}[/]  [bold #FFC247]⚡[/]  "
                    f"[#42E8F5]{evt.tool}[/][dim]({evt.args_preview})[/]  "
                    f"[{color}]{evt.status}[/]")
            self.events_log.write(line)

        def action_refresh(self) -> None:
            self.query_one("#status_body", Static).update(self._gather_status())
            self._refresh_agents()

        def action_clear_events(self) -> None:
            self.events_log.clear()

        def _gather_status(self) -> str:
            try:
                from argus import config as _cfg
                cfg = _cfg.load()
                from argus.tools.registry import register_defaults, available_tools
                register_defaults()
                from argus import paths
                mem_kb = (paths.MEMORY_MD.stat().st_size / 1024
                          if paths.MEMORY_MD.exists() else 0)
                user_kb = (paths.USER_MD.stat().st_size / 1024
                           if paths.USER_MD.exists() else 0)

                # Gateway PID alive?
                gw = "off"
                if paths.TELEGRAM_PID.exists():
                    try:
                        pid = int(paths.TELEGRAM_PID.read_text().strip())
                        os.kill(pid, 0)
                        gw = f"[green]running[/] (pid {pid})"
                    except (OSError, ValueError):
                        gw = "[red]stale pid[/]"

                lines = [
                    f"[dim]provider:[/]  [#F5E6C8]{cfg.agent.default_provider}/{cfg.agent.default_model}[/]",
                    f"[dim]memory:[/]    [#F5E6C8]{mem_kb:.1f} KB[/]  [dim]+ user[/] [#F5E6C8]{user_kb:.1f} KB[/]",
                    f"[dim]tools:[/]     [#F5E6C8]{len(available_tools())} registered[/]",
                    f"[dim]toolsets:[/]  [#F5E6C8]{', '.join(cfg.agent.toolsets[:6])}…[/]",
                    f"[dim]gateway:[/]   {gw}",
                    f"[dim]voice:[/]     [#F5E6C8]{cfg.voice.tts_provider}[/]  [dim]mode[/] [#F5E6C8]{cfg.voice.tts_mode}[/]",
                ]
                return "\n".join(lines)
            except Exception as e:
                return f"[red]status error: {e}[/]"

        def _refresh_agents(self) -> None:
            """Pull active swarm roles. Currently shows the role roster;
            future versions will hook live Constellation events."""
            try:
                from argus.swarm.roles import ROLES
                self.agents_log.clear()
                # Order by category
                cat_order = ["Architect", "Scout", "Engineer", "Cartographer",
                             "Auditor", "Medic", "Synthesizer",
                             "Scribe", "Ledger", "Atelier", "Herald",
                             "Analyst", "Sentinel", "Momento"]
                for name in cat_order:
                    if name not in ROLES: continue
                    role = ROLES[name]
                    glyph = _role_glyph(name)
                    self.agents_log.write(
                        f"[bold #FF38D1]{glyph}[/] [#F5E6C8]{name:<14}[/] "
                        f"[dim]{role.blurb[:38]}[/]"
                    )
            except Exception as e:
                self.agents_log.write(f"[red]agents error: {e}[/]")

    return ArgusDashboard()


def _role_glyph(name: str) -> str:
    return {
        "Architect":    "▲",
        "Scout":        "◐",
        "Engineer":     "⚙",
        "Cartographer": "◇",
        "Auditor":      "◉",
        "Medic":        "✚",
        "Synthesizer":  "✦",
        "Scribe":       "✎",
        "Ledger":       "▤",
        "Atelier":      "▦",
        "Herald":       "✉",
        "Analyst":      "Σ",
        "Sentinel":     "◈",
        "Momento":      "❖",
    }.get(name, "◆")

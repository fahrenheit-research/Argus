"""`argus sessions` — list, tree, resume, export, delete — PRD §13.4."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.padding import Padding
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from argus.data.seed import SESSIONS
from argus.screens._common import err_panel, hint, ok_panel, screen_header
from argus.theme import console as new_console


def _rel(dt: datetime) -> str:
    now = datetime(2026, 5, 30, 22, 13, tzinfo=timezone.utc)
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86_400:
        return f"{s // 3600}h ago"
    return f"{s // 86_400}d ago"


def list_sessions() -> None:
    console = new_console()
    screen_header(console, "Sessions", subtitle=f"{len(SESSIONS)} on disk · resume with `argus sessions resume <id|title>`")

    t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    t.add_column("ID", style="argus.dim", no_wrap=True)
    t.add_column("Title", style="argus.fg")
    t.add_column("Where", style="argus.cyan")
    t.add_column("Provider · Model", style="argus.dim")
    t.add_column("Last active", style="argus.cyan", no_wrap=True)
    t.add_column("Msgs", justify="right", style="argus.fg")
    t.add_column("Tokens (in / out)", justify="right", style="argus.gold", no_wrap=True)
    for s in SESSIONS:
        t.add_row(
            s.id,
            s.title,
            s.source,
            f"{s.provider} · {s.model}",
            _rel(s.last_active),
            f"{s.messages}",
            f"{s.tokens_in:>6,} / {s.tokens_out:>6,}",
        )
    console.print(t)


def tree() -> None:
    console = new_console()
    screen_header(console, "Session lineage", subtitle="parent → child via compression checkpoints.")

    # Build parent-id index
    by_parent: dict[str | None, list] = {}
    by_id = {s.id: s for s in SESSIONS}
    for s in SESSIONS:
        by_parent.setdefault(s.parent_id, []).append(s)

    root = Tree(Text("⟨◇⟩ all sessions", style="argus.heading"))

    def add(node: Tree, parent_id: str | None) -> None:
        for s in sorted(by_parent.get(parent_id, []), key=lambda x: x.last_active, reverse=True):
            label = Text()
            label.append(s.title, style="argus.fg")
            label.append("  ")
            label.append(s.id, style="argus.dim")
            label.append("  · ")
            label.append(f"{s.provider}/{s.model}", style="argus.cyan")
            child = node.add(label)
            add(child, s.id)

    add(root, None)
    console.print(Padding(root, (0, 2)))


def resume(ref: str) -> None:
    console = new_console()
    screen_header(console, "Session · resume")
    if not ref:
        err_panel(console, "Provide a session ID or fuzzy title.")
        return
    matches = [s for s in SESSIONS if ref.lower() in s.title.lower() or ref == s.id]
    if not matches:
        err_panel(console, f"no session matched '{ref}'.")
        return
    chosen = matches[0]
    ok_panel(
        console,
        f"resuming \"{chosen.title}\"",
        sub=f"{chosen.id} · {chosen.provider}/{chosen.model} · last active {_rel(chosen.last_active)}",
    )
    hint(console, "(would drop you into chat — chat REPL is `argus chat -r <id>`)")


def export(session_id: str) -> None:
    console = new_console()
    screen_header(console, "Session · export")
    match = next((s for s in SESSIONS if s.id == session_id), None)
    if not match:
        err_panel(console, f"no session with ID {session_id}.")
        return
    ok_panel(console, f"exported to ./{session_id}.jsonl", sub=f"{match.messages} messages.")


def delete(session_id: str) -> None:
    console = new_console()
    screen_header(console, "Session · delete")
    match = next((s for s in SESSIONS if s.id == session_id), None)
    if not match:
        err_panel(console, f"no session with ID {session_id}.")
        return
    ok_panel(console, f"deleted \"{match.title}\".", sub="MEMORY.md and USER.md are untouched.")

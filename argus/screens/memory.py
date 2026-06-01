"""`argus memory` — view, add, clear — PRD §13.1."""

from __future__ import annotations

from rich.columns import Columns
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from argus import picker
from argus.data.seed import MEMORY_MD_SAMPLE, USER_MD_SAMPLE
from argus.screens._common import hint, ok_panel, screen_header
from argus.theme import console as new_console


def show() -> None:
    console = new_console()
    screen_header(
        console,
        "Memory",
        subtitle="USER.md is your living profile. MEMORY.md is long-form journal & pinned facts.",
    )

    user_panel = Panel(
        Markdown(USER_MD_SAMPLE),
        title=Text("USER.md", style="argus.cyan"),
        border_style="argus.magenta",
        padding=(1, 2),
        width=48,
    )
    memory_panel = Panel(
        Markdown(MEMORY_MD_SAMPLE),
        title=Text("MEMORY.md", style="argus.cyan"),
        border_style="argus.magenta",
        padding=(1, 2),
        width=64,
    )
    console.print(Columns([user_panel, memory_panel], padding=(0, 1)))
    console.print()
    hint(console, "Add a fact:    argus memory add \"my new printer is on 10.0.0.41\"")
    hint(console, "Wipe:          argus memory clear")


def add(fact: str) -> None:
    console = new_console()
    screen_header(console, "Memory · add")
    if not fact.strip():
        from argus.screens._common import err_panel
        err_panel(console, "Provide a fact to add.")
        return
    ok_panel(console, "appended to MEMORY.md:", sub=f"- {fact}")
    hint(console, "(dry-run in this build — pass --write to persist)")


def clear() -> None:
    console = new_console()
    screen_header(console, "Memory · clear", subtitle="This wipes MEMORY.md and cannot be undone.")
    ok = picker.confirm("Are you sure you want to clear MEMORY.md?", default=False)
    if not ok:
        hint(console, "cancelled.")
        return
    ok_panel(console, "MEMORY.md cleared.", sub="USER.md is left untouched.")

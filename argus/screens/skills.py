"""`argus skills` — list, enable, disable, install — PRD §13.3."""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from argus.data.seed import SKILLS
from argus.screens._common import hint, ok_panel, screen_header
from argus.theme import console as new_console


def list_skills() -> None:
    console = new_console()
    screen_header(console, "Skills", subtitle=f"{len(SKILLS)} installed · SKILL.md format compatible with Hermes-Agent.")

    t = Table(show_header=True, header_style="argus.table.header", box=None, padding=(0, 2), pad_edge=False)
    t.add_column("", no_wrap=True)
    t.add_column("Name", style="argus.cyan", no_wrap=True)
    t.add_column("Description", style="argus.fg")
    t.add_column("Triggers", style="argus.dim")
    t.add_column("Tools", style="argus.gold")
    for s in SKILLS:
        glyph = Text("●", style="argus.ok") if s.enabled else Text("○", style="argus.dim")
        t.add_row(
            glyph,
            s.name,
            s.description,
            ", ".join(s.triggers),
            ", ".join(s.tools),
        )
    console.print(t)
    console.print()
    hint(console, "Enable / disable:  argus skills {enable,disable} <name>")
    hint(console, "Install a new one: argus skills install ./path/to/skill")


def set_enabled(name: str, enabled: bool) -> None:
    console = new_console()
    screen_header(console, f"Skills · {'enable' if enabled else 'disable'}")
    matches = [s for s in SKILLS if s.name == name]
    if not matches:
        from argus.screens._common import err_panel
        err_panel(console, f"no skill named '{name}' is installed.",
                  sub="Try `argus skills list` to see what's available.")
        return
    ok_panel(console, f"{'enabled' if enabled else 'disabled'} {name}.")


def install(source: str) -> None:
    console = new_console()
    screen_header(console, "Skills · install")
    hint(console, f"source: {source}")
    hint(console, "(install backend is stubbed in this build — would clone, validate SKILL.md, register)")
    ok_panel(console, "skill installed (dry-run).")

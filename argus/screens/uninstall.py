"""`argus uninstall` — PRD §7.4."""

from __future__ import annotations

from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from argus import picker
from argus.screens._common import hint, ok_panel, screen_header
from argus.theme import console as new_console


def run(purge: bool) -> None:
    console = new_console()
    screen_header(console, "Uninstall", subtitle="leaves no files outside your $HOME — PRD §7.4.")

    body = Text()
    body.append("Will remove:\n", style="argus.fg")
    body.append("  • ~/.argus/                — DB, sessions, skills, memory\n", style="argus.dim")
    body.append("  • ~/.local/bin/argus        — symlink\n", style="argus.dim")
    body.append("  • systemd / launchd unit    — if present\n", style="argus.dim")
    body.append("\nWill NOT touch:\n", style="argus.fg")
    body.append("  • the system Python\n", style="argus.dim")
    body.append("  • anything outside your $HOME\n", style="argus.dim")
    console.print(Padding(Panel(body, border_style="argus.err", padding=(1, 2)), (0, 0)))

    if not purge:
        ok = picker.confirm("Really uninstall?", default=False)
        if not ok:
            hint(console, "cancelled.")
            return

    ok_panel(console, "ARGUS removed.", sub="Thanks for trying it. ⟨◇⟩")

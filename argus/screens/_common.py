"""Shared layout primitives — screen header, section rule, status panels.

Every screen pulls from here so headers, rules, and one-line status banners
look identical without copy-pasting Rich snippets.
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from argus.glyph import glyph
from argus.theme import console as new_console

CON: Console = new_console()


def screen_header(console: Console, title: str, subtitle: str | None = None) -> None:
    """`⟨ ◇ ⟩  Title` over a magenta rule, optional subtitle below in dim."""
    line = Text()
    line.append_text(glyph("standard"))
    line.append("  ")
    line.append(title, style="argus.heading")
    console.print()
    console.print(line)
    console.print(Rule(style="argus.rule"))
    if subtitle:
        console.print(Text(subtitle, style="argus.dim"))
    console.print()


def kv_line(label: str, value: str, *, value_style: str = "argus.cyan") -> Text:
    t = Text()
    t.append(f"{label}: ", style="argus.dim")
    t.append(value, style=value_style)
    return t


def ok_panel(console: Console, message: str, *, sub: str | None = None) -> None:
    body = Text()
    body.append("✓ ", style="argus.ok")
    body.append(message, style="argus.fg")
    if sub:
        body.append("\n")
        body.append(sub, style="argus.dim")
    console.print(Padding(Panel(body, border_style="argus.gold", padding=(0, 2)), (1, 0)))


def err_panel(console: Console, message: str, *, sub: str | None = None) -> None:
    body = Text()
    body.append("✗ ", style="argus.err_text")
    body.append(message, style="argus.fg")
    if sub:
        body.append("\n")
        body.append(sub, style="argus.dim")
    console.print(Padding(Panel(body, border_style="argus.err", padding=(0, 2)), (1, 0)))


def hint(console: Console, text: str) -> None:
    console.print(Text(f"  {text}", style="argus.dim"))


def group(*items) -> Group:
    return Group(*items)

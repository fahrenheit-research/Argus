"""`argus tools` — toolset toggle screen."""

from __future__ import annotations

from argus import picker
from argus.screens._common import hint, ok_panel, screen_header
from argus.theme import console as new_console

_TOOLSETS = [
    ("memory",   "MEMORY.md + USER.md read/write",                  True),
    ("web",      "search, fetch, extract",                          True),
    ("files",    "workspace read/write (~/argus-workspace)",        True),
    ("shell",    "execute commands · per-call approval required",   True),
    ("github",   "list/get PRs, comment, diff",                     False),
    ("calendar", "Google Calendar read",                            False),
]


def run() -> None:
    console = new_console()
    screen_header(console, "Toolsets", subtitle="Space to toggle. Choices persist with --write (off in this build).")

    choices = [
        picker.Choice(value=name, label=name, description=blurb, checked=enabled)
        for name, blurb, enabled in _TOOLSETS
    ]
    selected = picker.multiselect("toolsets", choices)
    if selected is None:
        return
    ok_panel(console, f"enabled toolsets: {', '.join(selected) if selected else '(none)'}")
    hint(console, "Run `argus config set agent.toolsets '[memory,web,…]'` to persist.")

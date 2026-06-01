"""Custom prompt_toolkit-based pickers that match the Hermes-style UI.

Public API (kept identical to the questionary-shaped wrappers we had before
so callers don't have to change):

    select(message, choices, default=None, *, current=None) -> str | None
    multiselect(message, choices) -> list[str] | None
    text(message, default="") -> str | None
    password(message) -> str | None
    confirm(message, default=True) -> bool | None

Rendering rules (matches the screenshots):
    Select <message>:
    ↑↓ navigate   ENTER/SPACE select   ESC cancel

        (o)  First option
        (o)  Second option
    →   (●)  Currently focused option         ← currently active
        (o)  …

Multi-select uses `[ ]` and `[✓]`:
    Select <message>:
    ↑↓ navigate   SPACE toggle   ENTER confirm   ESC cancel

    →   [✓]  Already-on item
        [ ]  Off item
        [✓]  …

Each Choice carries a `value` (returned on select), `label` (rendered),
optional `disabled` reason, and optional `description` shown in parens
after the label.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Sequence

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, ScrollablePane
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.shortcuts import PromptSession, confirm as ptk_confirm
from prompt_toolkit.styles import Style

from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA

_PROMPT_STYLE = Style.from_dict(
    {
        "title": f"bold {GOLD}",
        "hint":  f"{DIM}",
        "row":   f"{FG}",
        "row.dim": f"{DIM}",
        "row.active": f"bold {GOLD}",
        "row.disabled": f"italic {DIM}",
        "pointer": f"bold {MAGENTA}",
        "marker": f"bold {MAGENTA}",
        "marker.on": f"bold {GOLD}",
        "marker.current": f"bold {GOLD}",
        "tag.current": f"bold {GOLD}",
        "input": f"{FG}",
        "prompt": f"bold {MAGENTA}",
    }
)


@dataclass
class Choice:
    value: str
    label: str
    description: str = ""        # shown in parens after the label
    disabled: str | None = None  # reason; renders the row dim and skips it
    submenu: bool = False        # appends `▸` after the label
    checked: bool = False        # initial state for multiselect


# ---------------------------------------------------------------------------
# Single-select (radio)
# ---------------------------------------------------------------------------

class _Selector:
    def __init__(
        self,
        message: str,
        choices: list[Choice],
        default: str | None,
        current: str | None,
        multi: bool,
    ) -> None:
        self.message = message
        self.choices = choices
        self.multi = multi
        self.current = current
        self.checked = {c.value for c in choices if c.checked} if multi else set()
        self.cancelled = False
        self.done = False

        # Index of the highlighted row.
        seed = default or current
        self.idx = next(
            (i for i, c in enumerate(choices) if c.value == seed and not c.disabled),
            next((i for i, c in enumerate(choices) if not c.disabled), 0),
        )

    # ---- rendering --------------------------------------------------------

    def _render(self) -> FormattedText:
        out: list[tuple[str, str]] = []
        out.append(("class:title", f"Select {self.message}:\n"))
        if self.multi:
            hint = "  ↑↓ navigate   SPACE toggle   ENTER confirm   ESC cancel\n\n"
        else:
            hint = "  ↑↓ navigate   ENTER/SPACE select   ESC cancel\n\n"
        out.append(("class:hint", hint))

        for i, c in enumerate(self.choices):
            if c.disabled:
                out.append(("class:row.disabled", f"      ( ) {c.label}  "))
                out.append(("class:row.disabled", f"({c.disabled})\n"))
                continue

            pointer = "  →   " if i == self.idx else "      "
            out.append(("class:pointer" if i == self.idx else "", pointer))

            # Marker
            if self.multi:
                mark = "[✓]" if c.value in self.checked else "[ ]"
                mark_class = "class:marker.on" if c.value in self.checked else "class:marker"
            else:
                is_current = c.value == self.current
                is_focus = i == self.idx
                if is_focus and is_current:
                    mark, mark_class = "(●)", "class:marker.current"
                elif is_current:
                    mark, mark_class = "(●)", "class:marker.current"
                elif is_focus:
                    mark, mark_class = "(●)", "class:marker"
                else:
                    mark, mark_class = "(o)", "class:row.dim"
            out.append((mark_class, mark + "  "))

            # Label
            row_class = "class:row.active" if i == self.idx else "class:row"
            label = c.label
            if c.submenu:
                label = label + " ▸"
            out.append((row_class, label))
            if c.description:
                out.append(("class:row.dim", f"  ({c.description})"))

            # Trailing "← currently active" tag for the radio current item.
            if not self.multi and c.value == self.current:
                out.append(("class:tag.current", "   ← currently active"))

            out.append(("", "\n"))

        return FormattedText(out)

    # ---- input ------------------------------------------------------------

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()

        def move(delta: int) -> None:
            n = len(self.choices)
            for _ in range(n):
                self.idx = (self.idx + delta) % n
                if not self.choices[self.idx].disabled:
                    return

        @kb.add("up")
        def _(event):
            move(-1)
            event.app.invalidate()

        @kb.add("down")
        def _(event):
            move(+1)
            event.app.invalidate()

        if self.multi:
            @kb.add(" ")
            def _(event):
                v = self.choices[self.idx].value
                if v in self.checked:
                    self.checked.remove(v)
                else:
                    self.checked.add(v)
                event.app.invalidate()

            @kb.add("enter")
            def _(event):
                self.done = True
                event.app.exit(result=sorted(self.checked))

        else:
            @kb.add("enter")
            @kb.add(" ")
            def _(event):
                self.done = True
                event.app.exit(result=self.choices[self.idx].value)

        @kb.add("escape")
        @kb.add("c-c")
        def _(event):
            self.cancelled = True
            event.app.exit(result=None)

        return kb

    # ---- driver -----------------------------------------------------------

    def run(self) -> str | list[str] | None:
        control = FormattedTextControl(self._render, focusable=True, show_cursor=False)
        window = Window(content=control, always_hide_cursor=True, wrap_lines=False)
        app = Application(
            layout=Layout(window),
            key_bindings=self._bindings(),
            style=_PROMPT_STYLE,
            mouse_support=False,
            full_screen=False,
            erase_when_done=False,
        )
        return app.run()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _normalise(choices: Sequence[str | Choice]) -> list[Choice]:
    out: list[Choice] = []
    for c in choices:
        if isinstance(c, Choice):
            out.append(c)
        else:
            out.append(Choice(value=c, label=c))
    return out


def select(
    message: str,
    choices: Sequence[str | Choice],
    default: str | None = None,
    *,
    current: str | None = None,
) -> str | None:
    if not sys.stdin.isatty():
        # Non-interactive fallback — return default or first non-disabled.
        cs = _normalise(choices)
        if default is not None:
            return default
        for c in cs:
            if not c.disabled:
                return c.value
        return None
    cs = _normalise(choices)
    return _Selector(message, cs, default, current, multi=False).run()  # type: ignore[return-value]


def multiselect(
    message: str,
    choices: Sequence[Choice],
) -> list[str] | None:
    if not sys.stdin.isatty():
        return [c.value for c in choices if c.checked and not c.disabled]
    return _Selector(message, list(choices), None, None, multi=True).run()  # type: ignore[return-value]


def text(message: str, default: str = "") -> str | None:
    if not sys.stdin.isatty():
        return default
    session: PromptSession = PromptSession()
    try:
        return session.prompt(
            FormattedText([("class:prompt", "⟨◇⟩ "), ("class:input", f"{message} ")]),
            default=default,
            style=_PROMPT_STYLE,
        )
    except (KeyboardInterrupt, EOFError):
        return None


def password(message: str) -> str | None:
    if not sys.stdin.isatty():
        return ""
    session: PromptSession = PromptSession()
    try:
        return session.prompt(
            FormattedText([("class:prompt", "⟨◇⟩ "), ("class:input", f"{message} ")]),
            is_password=True,
            style=_PROMPT_STYLE,
        )
    except (KeyboardInterrupt, EOFError):
        return None


def confirm(message: str, default: bool = True) -> bool | None:
    if not sys.stdin.isatty():
        return default
    suffix = " (Y/n): " if default else " (y/N): "
    session: PromptSession = PromptSession()
    try:
        raw = session.prompt(
            FormattedText([("class:prompt", "⟨◇⟩ "), ("class:input", f"{message}"), ("class:hint", suffix)]),
            style=_PROMPT_STYLE,
        )
    except (KeyboardInterrupt, EOFError):
        return None
    raw = raw.strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")

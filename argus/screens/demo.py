"""`argus demo` — narrated tour of every screen.

The single fastest way to see what ARGUS looks like end-to-end. Each screen
gets a one-line narration in dim text, then renders, then a short pause.
"""

from __future__ import annotations

import time

from rich.padding import Padding
from rich.rule import Rule
from rich.text import Text

from argus.banner import print_banner
from argus.glyph import glyph
from argus.screens import config as cfg
from argus.screens import doctor, gateway, memory, sessions, skills, update
from argus.slash import ChatState, dispatch
from argus.theme import console as new_console


def _narrate(console, msg: str) -> None:
    console.print()
    line = Text()
    line.append("⟨◇⟩ ", style="argus.bracket")
    line.append(msg, style="argus.dim")
    console.print(line)
    console.print(Rule(style="argus.panel.subtle"))


def run(fast: bool = False) -> None:
    console = new_console()
    pause = 0.0 if fast else 1.2

    # Intro
    console.print()
    console.print(Padding(glyph("display"), (0, 4)))
    console.print(Padding(Text("ARGUS · CLI design tour", style="argus.heading"), (0, 4)))
    console.print(Padding(Text("(every screen, in order, with one-line narration)", style="argus.dim"), (0, 4)))
    time.sleep(pause)

    # 1. Banner
    _narrate(console, "1 · banner — shown on `argus` with no subcommand")
    print_banner(console)
    time.sleep(pause)

    # 2. Chat — simulated turn (no real prompt loop)
    _narrate(console, "2 · chat REPL — slash commands are dispatched locally")
    state = ChatState()
    dispatch("/help", console, state)
    time.sleep(pause)
    dispatch("/status", console, state)
    time.sleep(pause)

    # 3. Doctor
    _narrate(console, "3 · doctor — diagnostics; mix of ✓ and ✗ surfaces remedies")
    doctor.run(force_all_green=False)
    time.sleep(pause)

    # 4. Doctor (all green)
    _narrate(console, "4 · doctor (all green) — after `argus setup` completes")
    doctor.run(force_all_green=True)
    time.sleep(pause)

    # 5. Sessions list + tree
    _narrate(console, "5 · sessions · list")
    sessions.list_sessions()
    time.sleep(pause)
    _narrate(console, "6 · sessions · tree (lineage from compression checkpoints)")
    sessions.tree()
    time.sleep(pause)

    # 7. Memory
    _narrate(console, "7 · memory · show (USER.md left, MEMORY.md right)")
    memory.show()
    time.sleep(pause)

    # 8. Skills
    _narrate(console, "8 · skills · list")
    skills.list_skills()
    time.sleep(pause)

    # 9. Gateway status
    _narrate(console, "9 · gateway · status — Telegram, the only adapter")
    gateway.status()
    time.sleep(pause)

    # 10. Gateway allow-list
    _narrate(console, "10 · gateway · allow --list")
    gateway.allow(user_id=None, list_only=True)
    time.sleep(pause)

    # 11. Gateway logs
    _narrate(console, "11 · gateway · logs")
    gateway.logs(n=10)
    time.sleep(pause)

    # 12. Config list (with masked secrets)
    _narrate(console, "12 · config · list — secrets always masked")
    cfg.list_config()
    time.sleep(pause)

    # 13. Update preview
    _narrate(console, "13 · update --check")
    update.run(check=True, channel="stable")
    time.sleep(pause)

    # Outro
    console.print()
    console.print(Padding(glyph("display"), (0, 4)))
    console.print(Padding(Text("end of tour · type `argus` to chat for real", style="argus.dim"), (0, 4)))
    console.print()

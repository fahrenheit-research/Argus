"""`argus reset` — wipe ~/.argus and start clean.

Use this when your ~/.argus is from a different project (e.g. the
"Agent Argus" repo had its own schema with `provider.default` instead
of `agent.default_provider`, and its .env had every key commented out).

Backs everything up to ~/.argus.bak-<timestamp> before deleting so you
never lose memory or sessions to a wrong button.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from argus import paths, picker
from argus.screens._common import err_panel, hint, ok_panel, screen_header
from argus.theme import console as new_console


def run(confirm: bool = False) -> None:
    console = new_console()
    screen_header(console, "Reset ~/.argus", subtitle="moves the existing dir aside, creates a clean one.")

    if not paths.HOME.exists():
        ok_panel(console, "~/.argus didn't exist — nothing to reset.")
        return

    if not confirm:
        proceed = picker.confirm(
            f"Move {paths.HOME} aside and start clean? (a backup is kept)",
            default=False,
        )
        if not proceed:
            hint(console, "cancelled.")
            return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = Path(str(paths.HOME) + f".bak-{stamp}")
    try:
        shutil.move(str(paths.HOME), str(backup))
    except OSError as e:
        err_panel(console, f"Couldn't move ~/.argus aside: {e}")
        return

    paths.ensure_dirs()
    ok_panel(
        console,
        f"reset complete — ~/.argus is fresh.",
        sub=f"Old contents backed up to {backup}\n\n"
            f"Next:  argus setup    (or)    argus key <provider> <api-key>",
    )

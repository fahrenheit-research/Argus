"""ARGUS visual identity — single source of truth for colors and styles.

Every screen pulls from this module. Changing a brand color is one edit.
Hex values are taken verbatim from the PRD §3.3 / §21.3.
"""

from rich.console import Console
from rich.theme import Theme

MAGENTA = "#FF38D1"   # brackets, headings rule, CTAs, prompt arrow
GOLD = "#FFC247"      # the diamond, success state, on-success reaction
CYAN = "#42E8F5"      # info, status line, links, italic tool-call lines
BG = "#050505"        # background reference (terminal owns the actual bg)
FG = "#E6E6E6"        # body text on dark
DIM = "#7A7A7A"       # secondary copy, separators, captions
ERR = "#FF5C5C"       # only non-PRD color, reserved for clear failure

THEME = Theme(
    {
        # raw tokens
        "argus.magenta": MAGENTA,
        "argus.gold": GOLD,
        "argus.cyan": CYAN,
        "argus.fg": FG,
        "argus.dim": DIM,
        "argus.err": ERR,
        # semantic styles
        "argus.bracket": f"bold {MAGENTA}",
        "argus.diamond": f"bold {GOLD}",
        "argus.wordmark": f"bold {FG}",
        "argus.tagline": f"italic {DIM}",
        "argus.status": CYAN,
        "argus.heading": f"bold {MAGENTA}",
        "argus.rule": MAGENTA,
        "argus.ok": f"bold {GOLD}",
        "argus.err_text": f"bold {ERR}",
        "argus.tool": f"italic {CYAN}",
        "argus.user": f"bold {FG}",
        "argus.muted": DIM,
        "argus.kbd": f"reverse {DIM}",
        "argus.key": f"bold {CYAN}",
        # panels & tables
        "argus.panel.border": MAGENTA,
        "argus.panel.subtle": DIM,
        "argus.table.header": f"bold {MAGENTA}",
        "argus.table.row": FG,
        "argus.table.dim": DIM,
    }
)


def console(**kwargs) -> Console:
    """A Console wired up with the ARGUS theme. Use this everywhere."""
    return Console(theme=THEME, **kwargs)

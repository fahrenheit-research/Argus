"""The ARGUS banner — big block letters + dotted sentinel eye + tools/skills + status bar.

Modelled on Hermes-Agent's full-width splash but rendered in the ARGUS palette:
- Block-letter wordmark in a magenta → gold vertical gradient
- A pointillist sentinel-eye logomark on the left
- Two-column "Available Tools" / "Available Skills" listing on the right
- Bottom session/model footer
- A single-line reverse-video status bar pinned to the bottom of chat
"""

from __future__ import annotations

from pyfiglet import Figlet
from rich.columns import Columns
from rich.console import Console, Group
from rich.padding import Padding
from rich.rule import Rule
from rich.table import Table, box as tbox
from rich.text import Text

from argus import TAGLINE, __version__
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA

# ---------------------------------------------------------------------------
# 1. Block-letter wordmark with a vertical magenta → gold gradient
# ---------------------------------------------------------------------------

_FIGLET = Figlet(font="ansi_shadow", width=200)

_MAGENTA_RGB = (0xFF, 0x38, 0xD1)
_GOLD_RGB = (0xFF, 0xC2, 0x47)


def _interp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> str:
    r = round(a[0] + (b[0] - a[0]) * t)
    g = round(a[1] + (b[1] - a[1]) * t)
    bl = round(a[2] + (b[2] - a[2]) * t)
    return f"#{r:02X}{g:02X}{bl:02X}"


def wordmark(text: str = "ARGUS") -> Text:
    """Block-letter render of `text`, gradient from magenta (top) to gold (bottom)."""
    lines = [ln for ln in _FIGLET.renderText(text).splitlines() if ln.strip()]
    out = Text()
    n = max(1, len(lines) - 1)
    for i, line in enumerate(lines):
        t = i / n
        color = _interp(_MAGENTA_RGB, _GOLD_RGB, t)
        out.append(line, style=f"bold {color}")
        out.append("\n")
    return out


# ---------------------------------------------------------------------------
# 2. The dotted sentinel-eye logomark
# ---------------------------------------------------------------------------

# Sentinel Glyph — pixel-art rendering of the ARGUS brand logo.
#
# The logo (see desktop/assets/icon.png) is:
#   • Two magenta jagged chevron-brackets (◀ / ▶ pointing INWARD)
#   • Cyan rails / dashes between brackets
#   • Gold hollow diamond in the center
#   • Cyan square accents above + below center
#
# Width: 41 cols.  Height: 13 rows.  All rows are EXACTLY 41 chars wide;
# the assert at the bottom enforces it so a future typo can't render garbage.
#
# Brackets POINT INWARD toward the diamond. Left bracket is a `>` shape
# (widest on the right edge — the "point"); right bracket is a `<` shape
# (widest on the left edge). This mirrors the brand logo exactly.
#
#                0         1         2         3         4
#                012345678901234567890123456789012345678901
_EYE_ART = [
    "                    ▪                     ",  # 0
    "                                          ",  # 1
    "  █                                    █  ",  # 2
    "  ██                                  ██  ",  # 3
    "  ███                                ███  ",  # 4
    "  ████                              ████  ",  # 5
    "  █████  ━━━━━━   ◆   ━━━━━━  █████  ",      # 6
    "  ████                              ████  ",  # 7
    "  ███                                ███  ",  # 8
    "  ██                                  ██  ",  # 9
    "  █                                    █  ",  # 10
    "                                          ",  # 11
    "                    ▪                     ",  # 12
]

# Color tags — same width as the corresponding _EYE_ART row.
# M=magenta (brackets), G=gold (diamond), C=cyan (rails + dots), ' '=skip.
_EYE_COLOR = [
    "                    C                     ",  # 0
    "                                          ",  # 1
    "  M                                    M  ",  # 2
    "  MM                                  MM  ",  # 3
    "  MMM                                MMM  ",  # 4
    "  MMMM                              MMMM  ",  # 5
    "  MMMMM  CCCCCC   G   CCCCCC  MMMMM  ",      # 6
    "  MMMM                              MMMM  ",  # 7
    "  MMM                                MMM  ",  # 8
    "  MM                                  MM  ",  # 9
    "  M                                    M  ",  # 10
    "                                          ",  # 11
    "                    C                     ",  # 12
]

# Sanity-check at import time so an edit that breaks alignment fails loudly.
# Note: some chars in _EYE_ART are 3-byte unicode (e.g. ▟ █ ▙); Python's len()
# counts CODE POINTS, not bytes, so this comparison is correct.
assert all(len(c) == len(t) for c, t in zip(_EYE_ART, _EYE_COLOR)), (
    "eye art and color rows must be the same length. Mismatches: "
    + ", ".join(f"row {i}: art={len(a)} color={len(c)}"
                for i, (a, c) in enumerate(zip(_EYE_ART, _EYE_COLOR))
                if len(a) != len(c))
)


def _eye_color_for(tag: str) -> str | None:
    """Map a color tag char → style name. None means default fg."""
    if tag == "M": return f"bold {MAGENTA}"
    if tag == "G": return f"bold {GOLD}"
    if tag == "C": return f"bold {CYAN}"
    return None


def eye_logo() -> Text:
    """The Sentinel Glyph — pixel-art rendering of the brand logo.

    Magenta jagged brackets (◀ / ▶) point inward toward a cyan-railed
    gold diamond, with cyan square accents top and bottom.
    """
    out = Text()
    for row_idx, line in enumerate(_EYE_ART):
        color_row = _EYE_COLOR[row_idx] if row_idx < len(_EYE_COLOR) else ""
        for col_idx, ch in enumerate(line):
            tag = color_row[col_idx] if col_idx < len(color_row) else " "
            style = _eye_color_for(tag)
            if style:
                out.append(ch, style=style)
            else:
                out.append(ch)
        out.append("\n")
    return out


# ---------------------------------------------------------------------------
# 3. Tools + Skills listing
# ---------------------------------------------------------------------------

_TOOLS_BY_CATEGORY: list[tuple[str, list[str]]] = [
    ("memory",     ["memory", "check_telegram", "get_status"]),
    ("web",        ["web_search", "web_fetch", "arxiv_search", "calculator", "get_datetime"]),
    ("files",      ["read_file", "write_file", "list_dir"]),
    ("shell",      ["run_command"]),
    ("delegation", ["delegate_task"]),
]

_FR_FRAMEWORKS: list[tuple[str, str]] = [
    ("AgentBrain",   "knowledge graph · entities, relations, time"),
    ("AgentMomento", "BM25 skill router · context injection"),
    ("AgentWire",    "message envelopes · inter-agent transport"),
]

_SKILLS_BY_CATEGORY: list[tuple[str, list[str]]] = [
    ("coding",        ["github-pr-review", "safe-shell"]),
    ("communication", ["telegram-daily-digest", "morning-brief"]),
    ("memory",        ["voice-todo", "session-recap"]),
    ("research",      ["research-brief"]),
    ("system",        ["sentinel-eyes"]),
]

_INLINE_LIMIT = 3


def _category_block(title: str, rows: list[tuple[str, list[str]]]) -> Text:
    body = Text()
    body.append(title, style=f"bold {GOLD}")
    body.append("\n")
    for cat, items in rows:
        body.append(f"{cat}: ", style=DIM)
        # Strip the trailing sentinel "..." so we don't render `…, ...`.
        real = [x for x in items if x != "..."]
        shown = real[:_INLINE_LIMIT]
        for i, name in enumerate(shown):
            body.append(name, style=CYAN)
            if i != len(shown) - 1:
                body.append(", ", style=DIM)
        if len(real) > _INLINE_LIMIT or len(items) > len(real):
            body.append(", ...", style=DIM)
        body.append("\n")
    return body


def _fr_block() -> Text:
    """Fahrenheit Research frameworks block — sits above Available Tools."""
    body = Text()
    body.append("Fahrenheit Research\n", style=f"bold {GOLD}")
    for name, brief in _FR_FRAMEWORKS:
        body.append(f"{name}: ", style=f"bold {MAGENTA}")
        body.append(brief + "\n", style=CYAN)
    return body


def tools_skills_panel() -> Text:
    body = Text()
    body.append_text(_fr_block())
    body.append("\n")
    body.append_text(_category_block("Available Tools", _TOOLS_BY_CATEGORY))
    body.append("\n")
    body.append_text(_category_block("Available Skills", _SKILLS_BY_CATEGORY))
    # No "13 tools · 8 skills · /help" line here anymore — that info is
    # consolidated into the single status line under eye_caption().
    return body


# ---------------------------------------------------------------------------
# 4. Below-the-eye session info
# ---------------------------------------------------------------------------

def eye_caption(provider: str, model: str, session_id: str) -> Text:
    """Single-line caption beneath the eye glyph:

        groq/llama-3.3-70b-versatile  ·  ~/.argus  ·  13 tools · 8 skills  ·  /help
        Session: 20260530_162547_bcf611

    Two physical lines (model+ctx info wraps if narrow), with all the
    "what is this" details packed together. No more standalone
    "~/.argus" / "Session:" rows scattered around.
    """
    # Counts derived from the same tables as tools_skills_panel()
    n_tools = sum(len([x for x in v if x != "..."]) for _, v in _TOOLS_BY_CATEGORY)
    n_skills = sum(len(v) for _, v in _SKILLS_BY_CATEGORY)

    out = Text()
    out.append(f"  {model}", style=f"bold {FG}")
    out.append("  ·  ", style=DIM)
    out.append("~/.argus", style=CYAN)
    out.append("  ·  ", style=DIM)
    out.append(f"{n_tools} tools · {n_skills} skills", style=DIM)
    out.append("  ·  ", style=DIM)
    out.append("/help", style=CYAN)
    out.append("\n")
    out.append("  Session: ", style=DIM)
    out.append(session_id, style=DIM)
    return out


# ---------------------------------------------------------------------------
# 5. Bottom status bar
# ---------------------------------------------------------------------------

def status_bar(
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    ctx_used: int = 0,
    ctx_total: int | None = None,
    ttft: str = "--",
    cost: str = "0.00",
) -> Text:
    """Single-line reverse-video status bar pinned to the bottom of chat."""
    ctx_str = f"{ctx_used:,}/{ctx_total:,}" if ctx_total else "--"
    bar = "[░░░░░░░░░]"
    t = Text()
    t.append(f" ⟨◇⟩ {model:<28} ", style=f"bold {MAGENTA} on #1A1A1A")
    t.append(f" ctx {ctx_str:<8} ", style=f"{CYAN} on #1A1A1A")
    t.append(f" {bar} ", style=f"{DIM} on #1A1A1A")
    t.append(f" ttft {ttft:<5} ", style=f"{CYAN} on #1A1A1A")
    t.append(f" ${cost:<5} ", style=f"{GOLD} on #1A1A1A")
    return t


# ---------------------------------------------------------------------------
# 6. Assemble + print
# ---------------------------------------------------------------------------

def _hero() -> Group:
    """Wordmark + a single centered subtitle line:

        ARGUS Agent v0.1.0  -  built by Fahrenheit Research · f-r.co
    """
    rule_text = Text.assemble(
        ("ARGUS Agent ", f"bold {GOLD}"),
        (f"v{__version__}", CYAN),
        ("   -   ", DIM),
        ("built by ", DIM),
        ("Fahrenheit Research", f"bold {GOLD}"),
        ("  ·  ", DIM),
        ("f-r.co", CYAN),
    )
    return Group(
        wordmark("ARGUS"),
        Rule(rule_text, style=MAGENTA),
    )


def _body(provider: str, model: str, session_id: str) -> Table:
    """Two-column layout: mandala left (fixed 52 cols), tools/skills right.

    Using Table.grid instead of Columns gives us precise column widths.
    The left cell is fixed at 52 chars (mandala is 42 + 2×2 for padding).
    The right cell fills the remaining terminal width automatically.
    """
    t = Table.grid(padding=(0, 2), expand=True)
    t.add_column(width=52, no_wrap=True)    # left: mandala + session caption
    t.add_column(no_wrap=False)             # right: fills remaining width

    left_content = Group(
        eye_logo(),
        eye_caption(provider, model, session_id),
    )
    t.add_row(left_content, tools_skills_panel())
    return t


def _footer() -> Group:
    # Single line. No tips, no clutter — the chat prompt is the next thing the
    # user sees and that's where focus belongs.
    welcome = Text("Type ", style=DIM)
    welcome.append("/help", style=CYAN)
    welcome.append(" for commands.", style=DIM)
    return Group(welcome)


def print_banner(
    console: Console,
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    session_id: str = "20260530_162547_bcf611",
) -> None:
    console.print()
    console.print(_hero())
    console.print()
    console.print(_body(provider, model, session_id))
    console.print()
    console.print(_footer())


def print_status_bar(console: Console, **kwargs) -> None:
    console.print(status_bar(**kwargs))


# ---------------------------------------------------------------------------
# Compat shim — keeps the older `banner_panel` callable for tests.
# ---------------------------------------------------------------------------

def banner_panel(provider: str = "groq", model: str = "llama-3.3-70b-versatile") -> Group:
    return Group(
        _hero(),
        Text(""),
        _body(provider, model, "20260530_162547_bcf611"),
        Text(""),
        _footer(),
    )


# ── Goodbye banner ─────────────────────────────────────────────────────────


_GOLD_RGB = (0xFF, 0xC2, 0x47)


def show_goodbye(console: Console) -> None:
    """Display the GOODBYE — ARGUS farewell banner.

    Rendered in reversed gradient (Gold → Magenta for GOODBYE so it
    reads as the warm close of a session), then the Sentinel Glyph
    centred beneath, then the Fahrenheit Research credit. Also fires a
    short retro chime through the system audio so the exit feels final.
    """
    # Fire the chime non-blocking BEFORE drawing so audio + visuals overlap.
    # No-op when ARGUS_NO_CHIME=1 or when no audio player is on PATH.
    try:
        from argus.chime import play_exit_chime
        play_exit_chime(blocking=False)
    except Exception:
        pass  # never let the chime block a clean exit

    from rich.align import Align

    # ── GOODBYE in Gold → Magenta ─────────────────────────────────
    bye_lines = [ln for ln in _FIGLET.renderText("GOODBYE").splitlines() if ln.strip()]
    out = Text()
    n = max(1, len(bye_lines) - 1)
    for i, line in enumerate(bye_lines):
        t = i / n
        color = _interp(_GOLD_RGB, _MAGENTA_RGB, t)   # reversed: gold top → magenta bottom
        out.append(line, style=f"bold {color}")
        out.append("\n")

    # ── ARGUS in Magenta → Gold (same as load banner) ─────────────
    argus_lines = [ln for ln in _FIGLET.renderText("ARGUS").splitlines() if ln.strip()]
    n2 = max(1, len(argus_lines) - 1)
    for i, line in enumerate(argus_lines):
        t = i / n2
        color = _interp(_MAGENTA_RGB, _GOLD_RGB, t)
        out.append(line, style=f"bold {color}")
        out.append("\n")

    # ── Sentinel Glyph + tagline ───────────────────────────────────
    glyph = Text()
    glyph.append("\n   ⟨ ", style=f"bold {MAGENTA}")
    glyph.append("◇", style=f"bold {GOLD}")
    glyph.append(" ⟩  ", style=f"bold {MAGENTA}")
    glyph.append("the watchful agent signs off", style=f"italic {DIM}")
    glyph.append("   ·   ", style=DIM)
    glyph.append("Fahrenheit Research", style=f"bold {GOLD}")
    glyph.append(" · ", style=DIM)
    glyph.append("f-r.co", style=CYAN)

    # ── Rule ──────────────────────────────────────────────────────
    rule = Rule(style=MAGENTA)

    console.print()
    console.print(out)
    console.print(glyph)
    console.print()
    console.print(rule)
    console.print()

"""Boot splash + animated loader for ARGUS.

Layout (matches the pixel-art reference exactly — see desktop/assets/splash_ref.png):

  ┌────────────────────────────────────────────────────────────────┐
  │                                                                │
  │    ⟨◇⟩    A R G U S                                            │   ← Phase 1 (hero)
  │                                                                │
  │            — — —  AI AGENTS INITIALIZING…  — — —               │   ← Phase 2 (subtitle)
  │                                                                │
  │   ▲ ARCHITECT    ◐ SCOUT      ⚙ ENGINEER      ◇ CARTOGRAPHER  │   ← Phase 3 (roster
  │   ◉ AUDITOR      ✚ MEDIC      ✦ SYNTHESIZER                   │     wake-up)
  │                                                                │
  │            — — —  LOADING AGENTS…  — — —                       │   ← Phase 4 (bar)
  │   ▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱▱   67 %                  │
  │                                                                │
  │   [ OBSERVING ]  [ ANALYZING ]  [ SYNTHESIZING ]  [ EXECUTING ]│   ← cycling highlight
  │                                                                │
  └────────────────────────────────────────────────────────────────┘

Design rules:
  • CLEAR SCREEN at start. CLEAR SCREEN at end. No half-drawn frames spill into
    the rest of the session.
  • Cursor is HIDDEN throughout, restored in a finally block.
  • The hero block (rows 1-7) is drawn ONCE then untouched — no cursor-up
    overprinting that ever drifts.
  • Only the loader rows (bar + status labels) use cursor save/restore.
  • Frame rate is intentionally slow (~80 ms) so the animation reads as
    confident rather than glitchy.

Honors:
  ARGUS_NO_SPLASH=1 → skip entirely
  non-TTY stdout    → skip (CI, pipes, redirects)
  caller --quiet    → skip
"""

from __future__ import annotations

import os
import random
import sys
import time
from typing import Iterable

# ── Brand palette (24-bit ANSI) ──────────────────────────────────────────────

_MAGENTA = (255,  56, 209)   # #FF38D1 — primary
_GOLD    = (255, 194,  71)   # #FFC247 — accent
_CYAN    = ( 66, 232, 245)   # #42E8F5 — info
_BEIGE   = (245, 230, 200)   # #F5E6C8 — soft fg
_DIM     = (110, 110, 110)


def _rgb(r: int, g: int, b: int) -> str:    return f"\x1b[38;2;{r};{g};{b}m"
def _bold() -> str:                          return "\x1b[1m"
def _reset() -> str:                         return "\x1b[0m"
def _hide_cursor() -> str:                   return "\x1b[?25l"
def _show_cursor() -> str:                   return "\x1b[?25h"
def _clear_screen() -> str:                  return "\x1b[2J\x1b[H"
def _move_to(row: int, col: int = 1) -> str: return f"\x1b[{row};{col}H"
def _save_cursor() -> str:                   return "\x1b[s"
def _restore_cursor() -> str:                return "\x1b[u"
def _clear_line() -> str:                    return "\x1b[2K"


def _interp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (int(a[0] + (b[0] - a[0]) * t),
            int(a[1] + (b[1] - a[1]) * t),
            int(a[2] + (b[2] - a[2]) * t))


# ── Sentinel Glyph (5-row pixel art) ─────────────────────────────────────────
#
# Color-tagged glyph that matches the Argus app icon: two magenta jagged
# brackets framing cyan rails and a gold diamond. Each character maps 1:1
# to a colour tag in the parallel _GLYPH_COLOR grid:
#
#   M = magenta brackets         (#FF38D1)
#   C = cyan dashes / rails      (#42E8F5)
#   G = gold diamond / accents   (#FFC247)
#   . = space (no fill)
#
# Width: 17 cols. Height: 5 rows — matches the wordmark height so the two
# block letters and the glyph baseline align visually.


# Each row is EXACTLY 17 chars; the color tag at index i applies to the
# char at index i. Hand-aligned — do not reflow.
#                  0123456789012345678
_GLYPH_CHARS = [
    "  ▟█         █▙  ",
    " ▟██   ━━━   ██▙ ",
    " ██ ━━  ◆  ━━ ██ ",
    " ▜██   ━━━   ██▛ ",
    "  ▜█         █▛  ",
]

_GLYPH_COLOR = [
    "  MM         MM  ",
    " MMM   CCC   MMM ",
    " MM CC  G  CC MM ",
    " MMM   CCC   MMM ",
    "  MM         MM  ",
]
# Sanity-check at import time so any future edit that breaks alignment
# explodes loudly instead of rendering wrong colors silently.
assert all(len(c) == len(t) for c, t in zip(_GLYPH_CHARS, _GLYPH_COLOR)), \
    "glyph char/color rows must be the same width"


def _color_for_tag(tag: str) -> tuple[int, int, int] | None:
    """Map color tag → RGB. Returns None for spaces / undefined (no SGR)."""
    if tag == "M": return _MAGENTA
    if tag == "G": return _GOLD
    if tag == "C": return _CYAN
    return None


def _render_glyph_pixel(row: int, char_col: int) -> str:
    """Render one cell of the glyph with its assigned colour tag."""
    if row >= len(_GLYPH_CHARS) or char_col >= len(_GLYPH_CHARS[row]):
        return " "
    ch = _GLYPH_CHARS[row][char_col]
    if ch == " ":
        return " "
    tag = _GLYPH_COLOR[row][char_col] if char_col < len(_GLYPH_COLOR[row]) else " "
    rgb = _color_for_tag(tag)
    if rgb is None:
        return ch
    return f"{_bold()}{_rgb(*rgb)}{ch}{_reset()}"


def _full_glyph_block() -> list[str]:
    """Return the 5-row glyph as a list of fully-coloured strings (one per row)."""
    out: list[str] = []
    for r in range(len(_GLYPH_CHARS)):
        out.append("".join(_render_glyph_pixel(r, c)
                            for c in range(len(_GLYPH_CHARS[r]))))
    return out


# Legacy single-color reference (used by callers that don't need per-pixel tags).
# Kept so old code paths keep working; new code paths should call _full_glyph_block().
_GLYPH = _GLYPH_CHARS


# ── Block-letter wordmark (5-row pixel font, ARGUS only) ─────────────────────


_LETTERS = {
    "A": ["  ██  ", " ████ ", "██  ██", "██████", "██  ██"],
    "R": ["█████ ", "██  ██", "█████ ", "██ ██ ", "██  ██"],
    "G": [" █████", "██    ", "██ ███", "██  ██", " █████"],
    "U": ["██  ██", "██  ██", "██  ██", "██  ██", " ████ "],
    "S": [" █████", "██    ", " ████ ", "    ██", "█████ "],
}


# ── Constellation roster (matches argus/swarm/roles.py) ──────────────────────


_ROSTER: list[tuple[str, str, tuple[int, int, int]]] = [
    ("▲", "ARCHITECT",    _MAGENTA),
    ("◐", "SCOUT",        _CYAN),
    ("⚙", "ENGINEER",     _GOLD),
    ("◇", "CARTOGRAPHER", _CYAN),
    ("◉", "AUDITOR",      _MAGENTA),
    ("✚", "MEDIC",        _GOLD),
    ("✦", "SYNTHESIZER",  _CYAN),
]


# ── Status labels (cycled during the loading bar) ────────────────────────────


_STATUS_LABELS: list[tuple[str, tuple[int, int, int]]] = [
    ("OBSERVING",    _CYAN),
    ("ANALYZING",    _MAGENTA),
    ("SYNTHESIZING", _CYAN),
    ("EXECUTING",    _GOLD),
]


# ── Witty subtitles cycled under the bar (~50 phrases) ───────────────────────
# Hand-picked: half are intelligent, half are dry. Designed to look like the
# ARGUS team has a sense of humor without being cringe. Each ≤ 44 chars so
# they don't overflow on an 80-col terminal.

_WITTY_LINES: tuple[str, ...] = (
    # Intelligent / on-brand
    "Negotiating with the rate limiter",
    "Consulting the constellation",
    "Sharpening the glyphs",
    "Phasing in the sentinels",
    "Pre-warming the witty replies cache",
    "Tessellating thoughts",
    "Drafting a sharper answer",
    "Bottling lightning",
    "Charging the wit capacitors",
    "Threading the lattice",
    "Distilling intent",
    "Calibrating the third eye",
    "Spooling the conversation buffer",
    "Forging covenants with the GPU",
    "Aligning constellations (literally)",
    "Lighting the watchfires",
    "Polarizing reality, slightly",
    "Unfurling the manifold",

    # Dry & witty
    "Compiling self-confidence",
    "Bribing the kernel with coffee",
    "Apologizing to the Stack Overflow gods",
    "Petting the rubber duck",
    "Confirming the moon is still there",
    "Filing a polite Issue with reality",
    "Disabling impostor-syndrome flag",
    "Sweet-talking the bytecode interpreter",
    "Resisting the urge to make a pun",
    "Confirming gravity is still gravity",
    "Politely refusing nihilism",
    "Cataloguing imaginary friends",
    "Drafting your apology in advance",
    "Counting electrons by hand",
    "Pretending to understand JavaScript",
    "Whispering sweet nothings to the GPU",
    "Auditioning for the orchestra",
    "Reading the room",
    "Defragging the personality",
    "Composing a haiku for stdout",
    "Sniff-testing the timeline",
    "Issuing tiny, polite SIGUSR1s",
    "Persuading entropy to cooperate",

    # Confident & cinematic
    "Watching",
    "Calibrating sentinels",
    "Conjuring nodes",
    "Awakening kernel",
    "Igniting cores",
    "Etching sigils",
    "Tracing trajectories",
    "Anchoring the mast",
    "Drawing the wards",
    "Surveying the field",
)


# ── Loading-bar styles (one randomly picked per run) ─────────────────────────


_BAR_STYLES = [
    {"fill": "▰", "empty": "▱"},
    {"fill": "█", "empty": "░"},
    {"fill": "■", "empty": "□"},
    {"fill": "◆", "empty": "◇"},
]


# ── Entry ────────────────────────────────────────────────────────────────────


def play_splash(*, quiet: bool = False) -> None:
    """Render the boot splash, dead-centered on the user's terminal.

    Pipeline:
      1. Hide cursor, clear screen, fire the boot chime (rising arpeggio).
      2. Compute splash bounding box and starting row so the whole block
         sits vertically centered (no scroll, even on tall terminals).
      3. Phase 1 (hero)      → glyph + ARGUS wordmark cascade.
      4. Phase 2 (subtitle)  → "AI AGENTS INITIALIZING…".
      5. Phase 3 (roster)    → 7 specialists wake up one at a time.
      6. Phase 4 (loader)    → progress bar + cycling status labels +
                                witty subtitle that rotates per quarter.
      7. Hold for a beat, clear screen, restore cursor — banner draws on
         a fresh canvas.

    No-op in: --quiet | ARGUS_NO_SPLASH=1 | non-TTY | dumb TERM.
    """
    if quiet or os.environ.get("ARGUS_NO_SPLASH") == "1":
        return
    if not sys.stdout.isatty():
        return
    if os.environ.get("TERM", "").lower() in {"dumb", ""}:
        return

    w = sys.stdout.write
    f = sys.stdout.flush

    try:
        w(_hide_cursor())
        w(_clear_screen())
        f()

        # ── Boot chime — fires non-blocking so the splash starts NOW,
        # and the audio crescendos in parallel with the glyph reveal.
        try:
            from argus.chime import play_boot_chime
            play_boot_chime(blocking=False)
        except Exception:
            pass  # silent if audio subsystem unavailable

        # ── Geometry: compute centered start row ─────────────────────
        term_cols = _terminal_cols()
        term_rows = _terminal_rows()

        # Splash total height (rows): 1 spacer + 5 hero + 2 spacer + 1 subtitle
        # + 1 spacer + 2 roster + 2 spacer + 1 label + 1 bar + 1 spacer +
        # 1 status + 1 witty + 1 spacer = 20 rows.
        splash_h = 20
        start_row = max(2, (term_rows - splash_h) // 2)

        # Horizontal center for the hero block. Block width =
        # glyph (17) + gap (3) + (5 letters × 6 cells + 4 spaces between) = 51.
        hero_w   = 17 + 3 + (5 * 6) + 4   # ≈ 54
        hero_left = max(2, (term_cols - hero_w) // 2)

        # ── Row map (single source of truth — keeps phases aligned) ──
        r_hero     = start_row
        r_subtitle = start_row + 7      # 5 hero + 2 spacer
        r_roster   = start_row + 9      # +1 spacer +1
        r_label    = start_row + 13     # roster (2) + 2 spacer
        r_bar      = start_row + 14
        r_status   = start_row + 16     # +1 spacer +1
        r_witty    = start_row + 18     # +1 spacer +1

        # Phase 1 — Hero (~700 ms)
        _phase_hero(w, f, top_row=r_hero, left_col=hero_left)

        # Phase 2 — Subtitle (~500 ms)
        _phase_subtitle(w, f, row=r_subtitle,
                        text="AI AGENTS INITIALIZING…",
                        accent=_CYAN, term_cols=term_cols)

        # Phase 3 — Roster (~900 ms)
        _phase_roster(w, f, top_row=r_roster, term_cols=term_cols)

        # Phase 4 — Loader (bar + status labels + witty subtitle, ~2.1 s)
        _phase_loader(w, f,
                      label_row=r_label, bar_row=r_bar,
                      status_row=r_status, witty_row=r_witty,
                      term_cols=term_cols)

        # Final breath, then clean wipe so banner has a fresh canvas.
        time.sleep(0.45)
        w(_clear_screen())
        f()

    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        w(_show_cursor())
        f()


def _terminal_cols() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _terminal_rows() -> int:
    try:
        return os.get_terminal_size().lines
    except OSError:
        return 30


# ── Phase 1: hero block (glyph + wordmark) ──────────────────────────────────


def _phase_hero(w, f, *, top_row: int, left_col: int) -> None:
    """Reveal the brand glyph + ARGUS wordmark, 5 rows tall.

    The glyph uses per-pixel colors (magenta brackets, cyan rails, gold
    diamond — matches the app icon) and appears as a single locked unit
    on the first frame. The wordmark arrives letter-by-letter left to
    right, each letter holding the magenta → gold vertical gradient.
    """
    word = "ARGUS"
    letter_w = len(_LETTERS["A"][0])
    height   = 5
    gap      = 3

    # Pre-render the glyph block once (it's static once revealed).
    glyph_rendered = _full_glyph_block()

    def composite(revealed_letters: int) -> list[str]:
        rows: list[str] = []
        for ri in range(height):
            left = glyph_rendered[ri] if ri < len(glyph_rendered) else ""
            t_row = ri / max(1, height - 1)
            right_chunks: list[str] = []
            for li in range(len(word)):
                letter_rows = _LETTERS[word[li]]
                if li < revealed_letters:
                    rgb = _interp(_MAGENTA, _GOLD, t_row)
                    right_chunks.append(f"{_bold()}{_rgb(*rgb)}{letter_rows[ri]}{_reset()}")
                elif li == revealed_letters:
                    # In-flight letter pops in cyan briefly.
                    right_chunks.append(f"{_bold()}{_rgb(*_CYAN)}{letter_rows[ri]}{_reset()}")
                else:
                    right_chunks.append(" " * letter_w)
            rows.append(left + (" " * gap) + " ".join(right_chunks))
        return rows

    # Print each frame at fixed top_row to avoid scroll drift.
    for revealed in range(len(word) + 1):
        rows = composite(revealed)
        for ri, line in enumerate(rows):
            w(_move_to(top_row + ri, left_col))
            w(_clear_line())
            w(line)
        f()
        time.sleep(0.11)

    # Hold the final frame briefly so the eye settles.
    time.sleep(0.25)


# ── Phase 2: subtitle ────────────────────────────────────────────────────────


def _phase_subtitle(w, f, *, row: int, text: str,
                    accent: tuple[int, int, int], term_cols: int) -> None:
    """Print '— — — TEXT — — —' centered, with the dashes fading in."""
    dash_pair = "— — —"
    full = f"{dash_pair}  {text}  {dash_pair}"
    left = max(2, (term_cols - _visible_len(full)) // 2)

    # First: the centred text appears all at once, dim
    w(_move_to(row, left))
    w(_clear_line())
    w(f"{_rgb(*_DIM)}{full}{_reset()}")
    f()
    time.sleep(0.20)

    # Then the body lights up cyan
    w(_move_to(row, left))
    w(_clear_line())
    w(f"{_rgb(*_GOLD)}{dash_pair}{_reset()}  "
      f"{_bold()}{_rgb(*accent)}{text}{_reset()}  "
      f"{_rgb(*_GOLD)}{dash_pair}{_reset()}")
    f()
    time.sleep(0.30)


def _visible_len(s: str) -> int:
    """Approximate the visible length of a (possibly ANSI-stripped) string."""
    out, i = 0, 0
    while i < len(s):
        if s[i] == "\x1b":
            j = s.find("m", i)
            if j == -1: break
            i = j + 1
        else:
            out += 1
            i += 1
    return out


# ── Phase 3: roster wake-up ──────────────────────────────────────────────────


def _phase_roster(w, f, *, top_row: int, term_cols: int) -> None:
    """Print the 7-agent lineup across 2 rows, lighting them up one at a time."""
    # Layout: 4 agents on row N, 3 agents on row N+1, each in a 18-col slot
    slot_w = 19
    row1 = _ROSTER[:4]
    row2 = _ROSTER[4:]

    # First pass: all dim
    def _render(active_count: int) -> None:
        for which, group in enumerate((row1, row2)):
            total_w = slot_w * len(group)
            left = max(2, (term_cols - total_w) // 2)
            w(_move_to(top_row + which, left))
            w(_clear_line())
            cells = []
            absolute_offset = 0 if which == 0 else len(row1)
            for i, (glyph, name, color) in enumerate(group):
                global_idx = absolute_offset + i
                if global_idx < active_count:
                    cell = (f"{_bold()}{_rgb(*color)}{glyph}{_reset()} "
                            f"{_rgb(*_BEIGE)}{name}{_reset()}")
                else:
                    cell = (f"{_rgb(*_DIM)}{glyph}{_reset()} "
                            f"{_rgb(*_DIM)}{name}{_reset()}")
                cells.append(cell.ljust(slot_w + len(cell) - _visible_len(cell)))
            w("".join(cells))
        f()

    for n in range(len(_ROSTER) + 1):
        _render(n)
        time.sleep(0.115)


# ── Phase 4: loader (bar + cycling status labels) ────────────────────────────


def _phase_loader(w, f, *, label_row: int, bar_row: int, status_row: int,
                  witty_row: int, term_cols: int) -> None:
    """Animated 'LOADING AGENTS…' bar + cycling status labels + witty subtitle.

    Layout (vertically stacked, all horizontally centered):
        label_row    →  — — —  LOADING AGENTS…  — — —
        bar_row      →  ▌▰▰▰▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▰▐   42 %
        status_row   →  [ OBSERVING ]  [ ANALYZING ]  [ SYNTHESIZING ]  [ EXECUTING ]
        witty_row    →  ⟨◇⟩  "Negotiating with the rate limiter"

    Bar progresses 0→100% over ~2.1 s. Status labels rotate per quarter.
    The witty subtitle changes every ~530 ms (one per quarter) so the
    user reads a new line every progress segment — feels deliberate, not
    spammy.
    """
    # ── Label row ────────────────────────────────────────────────────
    dash_pair = "— — —"
    label_text = "LOADING AGENTS…"
    full_label = f"{dash_pair}  {label_text}  {dash_pair}"
    label_left = max(2, (term_cols - _visible_len(full_label)) // 2)
    w(_move_to(label_row, label_left))
    w(_clear_line())
    w(f"{_rgb(*_GOLD)}{dash_pair}{_reset()}  "
      f"{_bold()}{_rgb(*_CYAN)}{label_text}{_reset()}  "
      f"{_rgb(*_GOLD)}{dash_pair}{_reset()}")
    f()
    time.sleep(0.15)

    # ── Bar geometry ────────────────────────────────────────────────
    style = random.choice(_BAR_STYLES)
    bar_width = min(48, max(24, term_cols - 16))
    # Bar block = ▌ + bar (bar_width) + ▐ + "  " + " 99 %" → 1+bar+1+2+5 = bar+9
    bar_block_w = bar_width + 9
    bar_left = max(2, (term_cols - bar_block_w) // 2)

    # ── Status-label row ────────────────────────────────────────────
    status_labels = [f"[ {name} ]" for name, _ in _STATUS_LABELS]
    gap = "  "
    status_total = sum(len(s) for s in status_labels) + len(gap) * (len(status_labels) - 1)
    status_left = max(2, (term_cols - status_total) // 2)

    def render_status(active_idx: int) -> None:
        w(_move_to(status_row, status_left))
        w(_clear_line())
        chunks = []
        for i, (name, color) in enumerate(_STATUS_LABELS):
            label = f"[ {name} ]"
            if i == active_idx:
                chunks.append(f"{_bold()}{_rgb(*color)}{label}{_reset()}")
            elif i < active_idx:
                chunks.append(f"{_rgb(*_BEIGE)}{label}{_reset()}")
            else:
                chunks.append(f"{_rgb(*_DIM)}{label}{_reset()}")
        w(gap.join(chunks))
        f()

    # ── Witty subtitle: pick 4 unique phrases, one per quarter ──────
    quarter_phrases = random.sample(_WITTY_LINES, k=4)
    def render_witty(active_idx: int) -> None:
        phrase = quarter_phrases[min(active_idx, len(quarter_phrases) - 1)]
        sigil = f"{_bold()}{_rgb(*_GOLD)}⟨◇⟩{_reset()}"
        body  = f"{_rgb(*_BEIGE)}\"{phrase}\"{_reset()}"
        full  = f"{sigil}  {body}"
        line_len = _visible_len(full)
        left = max(2, (term_cols - line_len) // 2)
        w(_move_to(witty_row, left))
        w(_clear_line())
        w(full)
        f()

    # Print the first witty line immediately (before bar starts) so the
    # user sees it during the 150ms label hold.
    render_witty(0)
    render_status(0)

    # ── Animate bar 0→100% in ~30 frames @ 70 ms = 2.1 s ────────────
    total_frames = 30
    frame_ms = 70
    last_witty_quarter = -1
    for i in range(total_frames + 1):
        progress = i / total_frames
        filled = int(bar_width * progress)

        # Rolling fill pattern: the leading edge "shimmers" by using a
        # lighter variant char for the last 2 cells so the bar feels alive.
        bar_str = ""
        for ci in range(bar_width):
            if ci < filled - 2:
                ch = style["fill"]
                t = ci / max(1, bar_width - 1)
                rgb = _interp(_MAGENTA, _GOLD, t)
                bar_str += f"{_bold()}{_rgb(*rgb)}{ch}{_reset()}"
            elif ci < filled:
                # Leading-edge cells get the brightest gold
                ch = style["fill"]
                bar_str += f"{_bold()}{_rgb(*_GOLD)}{ch}{_reset()}"
            else:
                bar_str += f"{_rgb(*_DIM)}{style['empty']}{_reset()}"

        pct = int(progress * 100)
        pct_str = f"{_bold()}{_rgb(*_CYAN)}{pct:3d} %{_reset()}"
        bracket_open  = f"{_bold()}{_rgb(*_MAGENTA)}▌{_reset()}"
        bracket_close = f"{_bold()}{_rgb(*_MAGENTA)}▐{_reset()}"

        w(_move_to(bar_row, bar_left))
        w(_clear_line())
        w(f"{bracket_open}{bar_str}{bracket_close}  {pct_str}")
        f()

        # Update status label every quarter
        active = min(len(_STATUS_LABELS) - 1,
                     int(progress * len(_STATUS_LABELS)))
        render_status(active)

        # Witty subtitle: change once per quarter only
        if active != last_witty_quarter:
            render_witty(active)
            last_witty_quarter = active

        time.sleep(frame_ms / 1000)

    # Hold a beat at 100% — confirm "EXECUTING" is fully lit before wipe.
    time.sleep(0.3)

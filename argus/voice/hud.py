"""ARGUS Voice HUD — two-column animated popup.

Layout (auto-adapts to any terminal size):

  ┌─────────────────────────────┬───────────────────────────────────────┐
  │                             │  ⟨◇⟩  ARGUS VOICE                    │
  │    L E F T                  │  ─────────────────────────────────    │
  │    A N I M A T I O N        │  ⟨◈⟩  LISTENING   recording voice    │
  │                             │  ─────────────────────────────────    │
  │  (responds to voice state   │                                       │
  │   and mic RMS level)        │  you    ›  What is the gold price?   │
  │                             │  argus  ▸  Gold is at $3,342 today.  │
  │                             │           Shall I track it for you?   │
  └─────────────────────────────┴───────────────────────────────────────┘

Left column  = animation (fills height, responds to state + mic RMS)
Right column = header, state badge, transcript

Fully responsive: reads os.get_terminal_size() every frame and reflows.
Renders at 12 fps.

State animations:
  ASLEEP    slow-breathing concentric rings + dim sigil
  ARMING    expanding cyan shockwaves from center
  LISTENING vertical spectral bars — height tracks mic RMS in real time
  THINKING  rotating constellation: 8 orbital dots + gold chaser
  SPEAKING  two overlapping golden sine waves scrolling left
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import sys
import time
from itertools import zip_longest
from pathlib import Path

# ── ANSI helpers ──────────────────────────────────────────────────────────────

def _rgb(r: int, g: int, b: int) -> str:
    return f"\x1b[38;2;{r};{g};{b}m"

def _lerp_rgb(a: tuple, b: tuple, t: float) -> str:
    t = max(0.0, min(1.0, t))
    return _rgb(int(a[0]+(b[0]-a[0])*t), int(a[1]+(b[1]-a[1])*t), int(a[2]+(b[2]-a[2])*t))

MAG  = _rgb(255,  56, 209)   # Electric Magenta
GOLD = _rgb(255, 194,  71)   # Acid Gold
CYAN = _rgb( 66, 232, 245)   # Ice Cyan
BEI  = _rgb(245, 230, 200)   # Beige
DIM  = _rgb(110, 110, 110)   # Dim
RST  = "\x1b[0m"
BLD  = "\x1b[1m"
CLR  = "\x1b[2J\x1b[H"
HIDE = "\x1b[?25l"
SHOW = "\x1b[?25h"

_MAG_T  = (255,  56, 209)
_GOLD_T = (255, 194,  71)
_CYAN_T = ( 66, 232, 245)
_DIM_T  = (110, 110, 110)

# ── Socket path ───────────────────────────────────────────────────────────────
SOCKET_PATH = str(Path.home() / ".argus" / "voice.sock")

# ── HUD state ─────────────────────────────────────────────────────────────────

class HUDState:
    def __init__(self):
        self.voice_status  = "asleep"
        self.hint          = "connecting…"
        self.transcript    : list[tuple[str, str]] = []
        self.rms           = 0.0
        self.tick          = 0
        self.connected     = False

# ── Term size ─────────────────────────────────────────────────────────────────

def _term() -> tuple[int, int]:
    try:
        c = os.get_terminal_size()
        return max(60, c.columns), max(20, c.lines)
    except Exception:
        return 120, 40

# ── Animation engines — each returns a list[str] of `h` rows, each `w` chars ─

def _pad(s: str, w: int) -> str:
    """Pad a string (possibly containing ANSI escapes) to visible width `w`."""
    import re
    visible = len(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s))
    return s + " " * max(0, w - visible)


def _anim_asleep(tick: int, rms: float, w: int, h: int) -> list[str]:
    rows = []
    cx, cy = w // 2, h // 2
    breath = (math.sin(tick / 100 * 2 * math.pi - math.pi / 2) + 1) / 2

    for row in range(h):
        cells = [" "] * w
        y = row - cy
        for ring_r, ring_phase, symbol in [(cx * 0.75, 0.0, "◇"),
                                             (cx * 0.45, 0.5, "◈"),
                                             (1.0,       1.0, "")]:
            b = (math.sin(tick / 100 * 2 * math.pi - math.pi / 2 + ring_phase * math.pi) + 1) / 2
            alpha = b * 0.85 + 0.15
            if ring_r <= 1.0:
                if row == cy:
                    sigil = f" ⟨◇⟩ "
                    sx = cx - len(sigil) // 2
                    color = _lerp_rgb(_DIM_T, _MAG_T, breath * 0.9) + BLD
                    for j, ch in enumerate(sigil):
                        if 0 <= sx + j < w:
                            cells[sx + j] = (color + ch + RST) if ch != " " else " "
            else:
                y_lim = max(1, int(ring_r * 0.5))
                if abs(y) <= y_lim:
                    cos_a = math.sqrt(max(0, 1 - (y / ring_r) ** 2))
                    for sign in (-1, 1):
                        x = cx + int(sign * ring_r * cos_a * 1.9)
                        if 0 <= x < w:
                            color = _lerp_rgb(_DIM_T, _CYAN_T, alpha * 0.7)
                            cells[x] = color + symbol + RST
        rows.append("".join(cells))
    return rows


def _anim_arming(tick: int, rms: float, w: int, h: int) -> list[str]:
    rows = []
    cx, cy = w // 2, h // 2
    for row in range(h):
        cells = [" "] * w
        y = row - cy
        for ring_i in range(5):
            ring_r = (tick * 0.5 + ring_i * 5) % (min(w, h) * 0.55)
            fade   = max(0.0, 1.0 - ring_r / (min(w, h) * 0.5))
            if ring_r > 0:
                cos_a = math.sqrt(max(0, 1 - (y / max(0.5, ring_r * 0.6)) ** 2)) if abs(y) <= ring_r * 0.6 else 0
                for sign in (-1, 1):
                    x = cx + int(sign * ring_r * 1.85 * cos_a)
                    if 0 <= x < w and fade > 0.05:
                        dot = "◈" if fade > 0.6 else ("◇" if fade > 0.3 else "·")
                        color = _lerp_rgb(_DIM_T, _CYAN_T, fade)
                        cells[x] = color + dot + RST
        if row == cy:
            s = f"⟨◈⟩"
            sx = cx - len(s) // 2
            for j, ch in enumerate(s):
                if 0 <= sx + j < w:
                    cells[sx + j] = f"{BLD}{CYAN}{ch}{RST}"
        rows.append("".join(cells))
    return rows


def _anim_listening(tick: int, rms: float, w: int, h: int) -> list[str]:
    rows = []
    n_bars = min(w - 2, 60)
    bar_w  = max(1, (w - 2) // n_bars)
    left   = (w - n_bars * bar_w) // 2
    level  = min(1.0, max(0.03, rms / 4500))
    bar_chars = "▁▂▃▄▅▆▇█"

    heights: list[int] = []
    for i in range(n_bars):
        phase  = (i / n_bars) * 2 * math.pi + tick * 0.20
        height = (math.sin(phase) + math.sin(phase * 1.7 + tick * 0.08) + 2) / 4
        heights.append(max(1 if level > 0.06 else 0, int(height * level * h)))

    for row in range(h):
        cells = [" "] * w
        row_idx = h - 1 - row   # 0 = bottom
        for i, bh in enumerate(heights):
            if bh > row_idx:
                x = left + i * bar_w
                t = row_idx / max(1, h - 1)
                color = _lerp_rgb(_CYAN_T, _MAG_T, t)
                ch    = bar_chars[min(7, max(0, bh - row_idx - 1 + 7))]
                for k in range(bar_w):
                    if 0 <= x + k < w:
                        cells[x + k] = (f"{BLD}{color}{ch}{RST}" if k == 0 else " ")
        rows.append("".join(cells))
    return rows


def _anim_thinking(tick: int, rms: float, w: int, h: int) -> list[str]:
    cx, cy = w // 2, h // 2
    orbit_rx = max(5, cx - 4)
    orbit_ry = max(2, cy - 2)
    n_dots   = 8
    speed    = tick * 0.045
    trail    = 4

    cell: dict[tuple[int, int], str] = {}

    def _pos(phase: float) -> tuple[int, int]:
        return (cx + int(math.cos(phase) * orbit_rx * 1.85),
                cy + int(math.sin(phase) * orbit_ry))

    for i in range(n_dots):
        phase = speed + i * (2 * math.pi / n_dots)
        x, y  = _pos(phase)
        color = GOLD if i == 0 else CYAN
        dot   = "◈" if i == 0 else "◇"
        if 0 <= x < w and 0 <= y < h:
            cell[(x, y)] = f"{BLD}{color}{dot}{RST}"

    for t in range(1, trail + 1):
        x, y = _pos(speed - t * 0.13)
        fade = 1.0 - t / (trail + 1)
        color = _lerp_rgb(_DIM_T, _GOLD_T, fade)
        if 0 <= x < w and 0 <= y < h:
            cell.setdefault((x, y), f"{color}·{RST}")

    spin_chars = "─╲│╱─╲│╱"
    spin = spin_chars[(tick // 3) % len(spin_chars)]

    rows = []
    for row in range(h):
        cells_list = [" "] * w
        for (cx2, cy2), ch_str in cell.items():
            if cy2 == row and 0 <= cx2 < w:
                cells_list[cx2] = ch_str
        if row == cy:
            sc = f" {spin}⟨◆⟩{spin} "
            sx = cx - len(sc) // 2
            for j, ch in enumerate(sc):
                if 0 <= sx + j < w:
                    color = CYAN if ch in "─╲│╱" else f"{BLD}{GOLD}"
                    cells_list[sx + j] = f"{color}{ch}{RST}"
        rows.append("".join(cells_list))
    return rows


def _anim_speaking(tick: int, rms: float, w: int, h: int) -> list[str]:
    bar_chars = "▁▂▃▄▅▆▇█"
    rows = []
    cy = h // 2

    for row in range(h):
        cells = [" "] * w
        y = row - cy
        for x in range(w):
            p1 = (x - tick * 1.1) / 5.5
            p2 = (x - tick * 0.65) / 9.0 + 1.4
            a1 = math.sin(p1) * max(2.0, cy * 0.75)
            a2 = math.sin(p2) * max(1.5, cy * 0.5)
            if abs(y - a1) < 0.9:
                t = 1.0 - abs(y - a1) / 0.9
                color = _lerp_rgb(_DIM_T, _GOLD_T, t)
                ch    = bar_chars[int(t * 7)]
                cells[x] = f"{BLD}{color}{ch}{RST}"
            elif abs(y - a2) < 0.65:
                t = 0.45 * (1.0 - abs(y - a2) / 0.65)
                color = _lerp_rgb(_DIM_T, _GOLD_T, t)
                cells[x] = f"{color}·{RST}"
        rows.append("".join(cells))
    return rows


_ANIM_FN = {
    "asleep":    _anim_asleep,
    "arming":    _anim_arming,
    "listening": _anim_listening,
    "thinking":  _anim_thinking,
    "speaking":  _anim_speaking,
}

# ── State metadata ────────────────────────────────────────────────────────────

_STATE_META = {
    "asleep":    (DIM,            "⟨◇⟩", "ASLEEP — say 'Hey Argus'",  "waiting for wake word"),
    "arming":    (f"{BLD}{CYAN}", "⟨◈⟩", "WAKING UP",                  "wake word detected"),
    "listening": (f"{BLD}{MAG}",  "●",   "● LISTENING — speak now",    "recording your voice"),
    "thinking":  (f"{BLD}{GOLD}", "◆",   "◆ THINKING…",                "processing"),
    "speaking":  (f"{BLD}{GOLD}", "▸",   "▸ ARGUS TALKING",            "say 'Argus stop' to interrupt"),
    "off":       (DIM,            "⟨◇⟩", "OFFLINE",                    "voice session ended"),
}

# ── Right-column transcript builder ──────────────────────────────────────────

def _build_right(state: HUDState, rw: int, rows: int) -> list[str]:
    """Build the right column.

    LAYOUT (top to bottom, with sticky status banner):
       ╭─────────────────────────╮
       │  ⟨◇⟩  ARGUS VOICE       │  ← header (2 rows)
       ├─────────────────────────┤
       │                         │
       │  ▸ ARGUS TALKING        │  ← STATUS BANNER (3 rows, BIG, pinned)
       │  say 'Argus stop'…      │
       │                         │
       ├─────────────────────────┤
       │  you   ›  …             │  ← scrolling transcript (rest of height)
       │  argus ▸  …             │     newest at the bottom; older drop off TOP
       │  you   ›  …             │
       ╰─────────────────────────╯

    The status banner NEVER scrolls. The transcript truncates so the badge
    stays in its fixed spot at the top, with words wrapping below.
    """
    import re
    def rule(w: int) -> str:
        return f"  {DIM}{'─' * (w - 3)}{RST}"

    status = state.voice_status or "asleep"
    color, glyph, label, default_hint = _STATE_META.get(status, _STATE_META["off"])
    hint = (state.hint or default_hint)[:rw - 6]

    # ── Sticky top section: header + BIG status banner ─────────────────────
    header: list[str] = []
    header.append(f"  {BLD}{GOLD}⟨◇⟩  ARGUS VOICE{RST}")
    header.append(rule(rw))
    header.append("")
    # BIG status — bold, prefixed with colored full block, fills the line
    block = f"{color}██{RST}"
    header.append(f"  {block}  {color}{label[:rw - 8]}{RST}")
    # Hint underneath in dim color so the user knows what to do
    header.append(f"      {DIM}{hint}{RST}")
    header.append(rule(rw))
    header.append("")

    # ── Render transcript (scrolls below; newest at bottom) ────────────────
    avail = max(2, rows - len(header) - 1)

    # Word-wrap ALL entries first, then keep only the last `avail` rendered lines
    wrapped: list[str] = []
    for role, text in state.transcript:
        if role == "user":
            prefix   = f"  {CYAN}{BLD}you  {RST} {DIM}›{RST}  "
            body_clr = BEI
        elif role == "argus":
            prefix   = f"  {GOLD}{BLD}argus{RST} {DIM}▸{RST}  "
            body_clr = GOLD
        else:
            prefix   = f"  {DIM}⟨◇⟩{RST}   "
            body_clr = DIM
        max_w = rw - 14
        words, buf, first = text.split(), "", True
        for word in words:
            if len(buf) + len(word) + 1 > max_w:
                p = prefix if first else " " * 12
                wrapped.append(f"{p}{body_clr}{buf}{RST}")
                buf = word; first = False
            else:
                buf = (buf + " " + word).strip()
        if buf:
            wrapped.append(f"{(prefix if first else '      ')}{body_clr}{buf}{RST}")

    return header + wrapped[-avail:]


# ── Main renderer ─────────────────────────────────────────────────────────────

def _render(state: HUDState) -> None:
    cols, rows = _term()

    # ALWAYS two-column layout (animation left, transcript right).
    # The popup window is sized to give at least ~90 cols of width, so this
    # always works. Very narrow terminals (<60 cols) fall back to compact
    # single-column as a safety net only.
    if cols < 60:
        _render_single_column(state, cols, rows)
    else:
        _render_two_column(state, cols, rows)
    state.tick += 1


def _render_single_column(state: HUDState, w: int, rows: int) -> None:
    """Compact single-column layout for the 300×300 small window.

    Animation block fills the top ~half; state badge + transcript below.
    """
    status = state.voice_status or "asleep"
    fn     = _ANIM_FN.get(status, _anim_asleep)

    # Allocate rows: header(2) + animation(40%) + rule(1) + badge(1) + rule(1) + transcript(rest)
    anim_h     = max(5, int(rows * 0.40))
    header_h   = 2
    fixed_h    = header_h + anim_h + 3   # +3 for two rules + state badge
    trans_h    = max(2, rows - fixed_h - 1)

    anim = fn(state.tick, state.rms, w, anim_h)

    output: list[str] = []

    # Header
    output.append(f"{BLD}{GOLD}⟨◇⟩ ARGUS{RST}  {DIM}close to stop{RST}")
    output.append(f"{DIM}{'─' * w}{RST}")

    # Animation
    output.extend(anim)

    # State badge
    color, glyph, label, default_hint = _STATE_META.get(status, _STATE_META["off"])
    hint = (state.hint or default_hint)[:w - 12]
    output.append(f"{DIM}{'─' * w}{RST}")
    output.append(f"{color}{glyph} {label}{RST} {DIM}{hint}{RST}")
    output.append(f"{DIM}{'─' * w}{RST}")

    # Transcript
    entries = state.transcript[-trans_h:]
    for role, text in entries:
        if role == "user":
            prefix = f"{CYAN}{BLD}you{RST} {DIM}›{RST} "
            body_c = BEI
        elif role == "argus":
            prefix = f"{GOLD}{BLD}arg{RST} {DIM}▸{RST} "
            body_c = GOLD
        else:
            prefix = f"{DIM}⟨◇⟩{RST} "
            body_c = DIM
        max_w = max(1, w - 6)
        words, buf, first = text.split(), "", True
        for word in words:
            if len(buf) + len(word) + 1 > max_w:
                p = prefix if first else "      "
                output.append(f"{p}{body_c}{buf[:max_w]}{RST}")
                buf = word; first = False
            else:
                buf = (buf + " " + word).strip()
        if buf:
            output.append(f"{(prefix if first else '      ')}{body_c}{buf[:max_w]}{RST}")

    sys.stdout.write(CLR + "\n".join(output) + "\n")
    sys.stdout.flush()


def _render_two_column(state: HUDState, cols: int, rows: int) -> None:
    """Full two-column layout for wide terminals."""
    import re
    def vis_pad(s: str, target_w: int) -> str:
        vl = len(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", s))
        return s + " " * max(0, target_w - vl)

    lw = max(30, int(cols * 0.45))
    rw = max(20, cols - lw - 3)
    h  = max(8, rows - 2)

    status = state.voice_status or "asleep"
    fn     = _ANIM_FN.get(status, _anim_asleep)
    anim   = fn(state.tick, state.rms, lw, h)
    right  = _build_right(state, rw, h)

    output: list[str] = []
    for left_row, right_row in zip_longest(anim, right, fillvalue=" " * lw):
        output.append(f"{vis_pad(left_row or '', lw)}  {DIM}│{RST}  {right_row or ''}")

    sys.stdout.write(CLR + "\n".join(output) + "\n")
    sys.stdout.flush()


# ── Socket client ─────────────────────────────────────────────────────────────

async def _connect_with_retry(retries: int = 50, delay: float = 0.25) -> tuple | None:
    for _ in range(retries):
        try:
            return await asyncio.open_unix_connection(SOCKET_PATH)
        except (FileNotFoundError, ConnectionRefusedError):
            await asyncio.sleep(delay)
    return None


async def run_hud() -> None:
    state = HUDState()
    state.hint = "connecting to voice loop…"
    _render(state)

    pair = await _connect_with_retry()
    if pair is None:
        state.voice_status = "off"
        state.hint         = "could not connect — run `argus talk` in the main window"
        state.transcript.append(("info",
            "Start voice mode: type  argus talk  in the main ARGUS window."))
        _render(state)
        await asyncio.sleep(10)
        return

    reader, writer = pair
    state.connected = True
    state.hint      = ""

    async def _event_reader():
        try:
            async for raw in reader:
                if not raw: break
                try:
                    evt = json.loads(raw.decode().strip())
                except Exception:
                    continue
                t = evt.get("t", "")
                if t == "state":
                    state.voice_status = evt.get("status", "asleep")
                    state.hint         = evt.get("hint", "")
                elif t in ("user", "argus", "info"):
                    txt = evt.get("text", "").strip()
                    if txt:
                        state.transcript.append((t, txt))
                elif t == "level":
                    state.rms = float(evt.get("rms", 0.0))
        except (asyncio.CancelledError, Exception):
            pass

    async def _render_loop():
        while True:
            _render(state)
            await asyncio.sleep(1 / 12)   # 12 fps

    reader_task = asyncio.create_task(_event_reader())
    render_task = asyncio.create_task(_render_loop())
    try:
        await reader_task
    finally:
        render_task.cancel()
        try: writer.close()
        except Exception: pass

    state.voice_status = "off"
    state.hint         = "session ended — close this window"
    _render(state)
    await asyncio.sleep(5)


# ── Entry ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sys.stdout.write(HIDE)
    sys.stdout.flush()

    def _bye(*_):
        sys.stdout.write(f"{CLR}{SHOW}\n")
        sys.stdout.flush()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT,  _bye)

    try:
        asyncio.run(run_hud())
    except SystemExit:
        pass
    finally:
        sys.stdout.write(SHOW)
        sys.stdout.flush()


if __name__ == "__main__":
    main()

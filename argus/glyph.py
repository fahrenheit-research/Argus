"""The Sentinel Glyph — `⟨ ◇ ⟩`.

Left bracket  — incoming signals
Diamond       — the ARGUS intelligence core
Right bracket — outbound actions

Three sizes per PRD §21.1.
"""

from typing import Literal

from rich.text import Text

Size = Literal["display", "standard", "inline"]

_SPACINGS: dict[Size, tuple[str, str]] = {
    "display":  ("   ", "   "),  # ⟨   ◇   ⟩
    "standard": (" ", " "),       # ⟨ ◇ ⟩
    "inline":   ("", ""),         # ⟨◇⟩
}


def glyph(size: Size = "standard") -> Text:
    """Return a Rich Text of the Sentinel Glyph at the requested size."""
    left_gap, right_gap = _SPACINGS[size]
    t = Text()
    t.append("⟨", style="argus.bracket")
    t.append(left_gap)
    t.append("◇", style="argus.diamond")
    t.append(right_gap)
    t.append("⟩", style="argus.bracket")
    return t


def wordmark() -> Text:
    """`A R G U S` — spaced caps, body color."""
    return Text("A R G U S", style="argus.wordmark")


def glyph_and_mark(size: Size = "standard") -> Text:
    """`⟨ ◇ ⟩   A R G U S` — used in the banner and screen headers."""
    out = glyph(size)
    out.append("   ")
    out.append_text(wordmark())
    return out


def inline_prompt_glyph() -> Text:
    """`⟨◇⟩` for inline use (log prefix, list bullets, status messages)."""
    return glyph("inline")

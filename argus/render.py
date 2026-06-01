"""Output sanitizer + brand-styled markdown renderer.

Two contracts the agent now obeys EVERYWHERE it talks to the user:

  1. NO EM DASHES — every `—` becomes ` - `. (Same for en-dashes `–`.)
     The model loves them; users don't. Stripping at the output layer
     means we never have to rely on the prompt obeying.

  2. NO RAW MARKDOWN SYNTAX — `**bold**` / `__italic__` / `# Header` /
     `` `code` `` symbols never appear in the rendered output. They
     become real styling:
        • `**bold**`        → Rich bold MAGENTA  (FF38D1)
        • `# Title`         → bold GOLD          (FFC247)
        • `## Subtitle`     → bold CYAN          (42E8F5)
        • `### Section`     → bold CYAN
        • `` `code` ``      → CYAN, no syntax
        • `- bullet`        → cyan bullet `•` + clean text
        • Numbered lists    → magenta number + clean text

The agent's output stream still emits markdown — we just parse + style at
render time. Works for both CLI (Rich Text) and Telegram (plain UTF-8 +
Telegram MarkdownV2 escape rules).

Importable helpers:
  - `clean_text(s)`           → strip em-dashes + collapse whitespace, no styling
  - `render_to_rich_text(s)`  → returns a `rich.text.Text` with brand colors
  - `render_for_telegram(s)`  → returns a plain-UTF8 string with simple bold
"""

from __future__ import annotations

import re

# Brand colors — single source of truth (mirrors argus/theme.py)
_MAGENTA = "#FF38D1"
_GOLD    = "#FFC247"
_CYAN    = "#42E8F5"
_BEIGE   = "#F5E6C8"
_DIM     = "#7A7A7A"


# ── Em-dash / smart-quote / other typographic-noise scrubber ────────────────


_PUNCT_REPLACEMENTS = {
    "—": " - ",   # em dash —
    "–": " - ",   # en dash –
    "−": "-",     # minus sign
    "‘": "'",     # left single quote
    "’": "'",     # right single quote / apostrophe
    "“": '"',     # left double quote
    "”": '"',     # right double quote
    "…": "...",   # ellipsis
    " ": " ",     # non-breaking space
}


def _scrub_punct(s: str) -> str:
    """Strip em-dashes, smart quotes, NBSP, ellipsis chars. Always called
    before any other processing."""
    for src, dst in _PUNCT_REPLACEMENTS.items():
        s = s.replace(src, dst)
    # Collapse the double-space artefact em-dash replacement leaves
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r" -  ", " - ", s)
    return s


def clean_text(s: str) -> str:
    """Public sanitizer. Strips em-dashes etc. No markdown processing."""
    return _scrub_punct(s)


# ── Markdown → Rich Text (for CLI) ──────────────────────────────────────────


# Order matters — process from longest patterns first so `###` doesn't
# match before `#` etc. We use lazy spans to keep the renderer fast on
# streaming partials.
_HEADER_RE   = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_BOLD_RE     = re.compile(r"\*\*([^\*\n]+?)\*\*")
_ITALIC_RE   = re.compile(r"(?<!\*)\*([^\*\n]+?)\*(?!\*)")
_CODE_RE     = re.compile(r"`([^`\n]+?)`")
_LINK_RE     = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_FENCED_CODE = re.compile(r"```[a-zA-Z0-9_+-]*\n([\s\S]*?)```")
_HRULE_RE    = re.compile(r"^[-=_]{3,}\s*$", re.MULTILINE)
_BULLET_RE   = re.compile(r"^[\s]{0,4}[\-\*\+]\s+(.+?)$", re.MULTILINE)
_NUM_RE      = re.compile(r"^[\s]{0,4}(\d+)\.\s+(.+?)$", re.MULTILINE)

# ── Auto-link detection ───────────────────────────────────────────────────────
# URLs and email addresses are auto-converted to clickable hyperlinks.
# Color: Ice Cyan #42E8F5 — distinct from Gold (headings), Magenta (bold),
# Beige (body text). Underlined so they're obviously interactive.

_AUTO_URL_RE = re.compile(
    r"(?<!\[)"                            # not already inside a markdown link
    r"(https?://[^\s<>\"{}|\\^`\[\]()]+)" # http/https URL
    r"(?!\])",                             # not already a markdown link target
    re.IGNORECASE,
)
_AUTO_EMAIL_RE = re.compile(
    r"\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b"
)

_LINK_STYLE = f"underline {_CYAN}"   # Ice Cyan underlined — the "hyperlink" color


def render_to_rich_text(s: str) -> "Text":
    """Convert markdown-flavoured text into a styled Rich Text object.

    Brand-color rules:
      • H1 (`#`)        → bold GOLD
      • H2 (`##`)       → bold CYAN
      • H3+ (`###+`)    → bold MAGENTA
      • **bold**        → bold MAGENTA
      • *italic*        → italic CYAN
      • `code`          → CYAN
      • [text](url)     → cyan underline
      • ```fence```     → cyan block on darker bg
      • - bullet        → magenta •  + beige text
      • 1. numbered     → magenta number + beige text
      • horizontal rule → dim ──── line
    """
    from rich.text import Text

    s = _scrub_punct(s)
    out = Text()

    # Walk the text line-by-line so multi-pattern matches don't fight.
    for raw_line in s.split("\n"):
        # ── Horizontal rule ──
        if _HRULE_RE.match(raw_line):
            out.append("─" * 50, style=_DIM)
            out.append("\n")
            continue

        # ── Headers ──
        m = _HEADER_RE.match(raw_line)
        if m:
            level = len(m.group(1))
            title = m.group(2)
            if level == 1:
                style = f"bold {_GOLD}"
            elif level == 2:
                style = f"bold {_CYAN}"
            else:
                style = f"bold {_MAGENTA}"
            out.append(title, style=style)
            out.append("\n")
            continue

        # ── Bullet ──
        m = _BULLET_RE.match(raw_line)
        if m:
            out.append("  • ", style=_MAGENTA)
            _append_inline(out, m.group(1))
            out.append("\n")
            continue

        # ── Numbered ──
        m = _NUM_RE.match(raw_line)
        if m:
            out.append(f"  {m.group(1)}. ", style=f"bold {_MAGENTA}")
            _append_inline(out, m.group(2))
            out.append("\n")
            continue

        # ── Default paragraph line ──
        _append_inline(out, raw_line)
        out.append("\n")

    # Drop trailing newline — callers usually want bare text
    if out.plain.endswith("\n"):
        # Rich.Text has no rstrip; remove last span if it's a sole newline
        if out._text and out._text[-1] == "\n":
            out._text.pop()
            if out._spans and out._spans[-1].end > len(out.plain):
                out._spans.pop()

    return out


def _append_inline(out: "Text", line: str) -> None:
    """Apply inline markdown (**bold**, *italic*, `code`, [link](url)) and
    auto-link bare URLs/emails to `out`. Scans left-to-right, takes the
    leftmost match, plain text segments appended as Beige.

    URLs and email addresses become Ice Cyan (#42E8F5) underlined clickable
    links using OSC 8 sequences where supported (iTerm2, modern Terminal.app).
    """
    from rich.text import Text

    pos = 0
    while pos < len(line):
        candidates: list[tuple[int, "re.Match", str]] = []
        for pat, kind in ((_BOLD_RE,       "bold"),
                          (_CODE_RE,       "code"),
                          (_LINK_RE,       "link"),
                          (_ITALIC_RE,     "italic"),
                          (_AUTO_URL_RE,   "auto_url"),
                          (_AUTO_EMAIL_RE, "auto_email")):
            m = pat.search(line, pos)
            if m:
                candidates.append((m.start(), m, kind))
        if not candidates:
            out.append(line[pos:], style=_BEIGE)
            break
        candidates.sort(key=lambda x: x[0])
        first_pos, m, kind = candidates[0]

        # Plain text before the match
        if first_pos > pos:
            out.append(line[pos:first_pos], style=_BEIGE)

        if kind == "bold":
            out.append(m.group(1), style=f"bold {_MAGENTA}")
        elif kind == "italic":
            out.append(m.group(1), style=f"italic {_CYAN}")
        elif kind == "code":
            out.append(m.group(1), style=_CYAN)
        elif kind == "link":
            txt, url = m.group(1), m.group(2)
            full_url = url if url.startswith(("http://", "https://", "mailto:")) else f"https://{url}"
            out.append(txt, style=f"{_LINK_STYLE} link {full_url}")
        elif kind == "auto_url":
            url = m.group(1)
            full_url = url if url.startswith(("http://", "https://")) else f"https://{url}"
            out.append(url, style=f"{_LINK_STYLE} link {full_url}")
        elif kind == "auto_email":
            email = m.group(1)
            out.append(email, style=f"{_LINK_STYLE} link mailto:{email}")

        pos = m.end()


# ── Markdown → Telegram-safe (for the gateway) ──────────────────────────────


# Telegram MarkdownV2 reserves these chars: _*[]()~`>#+-=|{}.!
# Only the ones inside formatting need escaping; ours are clean enough
# that we just sanitize + leave standard `*bold*` working.
_TG_RESERVED = r'_*[]()~`>#+-=|{}.!\\'


def render_for_telegram(s: str) -> str:
    """Sanitize + reshape markdown for Telegram's parse_mode=MARKDOWN.

    Strategy:
      • Strip em-dashes etc.
      • Convert `**bold**` → `*bold*` (Telegram legacy markdown style).
      • Convert `# Title` → `*Title*` (no header levels in Telegram markdown).
      • Convert `## Sub`  → `*Sub*`.
      • Keep `` `code` `` as-is — Telegram supports it.
      • Convert `- bullet` → `• bullet`.
      • Convert numbered lists → keep digits, prepend the bullet style.

    Telegram's legacy MARKDOWN parser is forgiving; we use it (not MarkdownV2)
    to avoid escaping every period.
    """
    s = _scrub_punct(s)

    # Fenced code: keep as triple-backtick blocks (Telegram renders them)
    # — but only the inner content matters; the language hint is OK to keep.

    # Headers → bold (one level only on TG)
    s = re.sub(r"^#{1,6}\s+(.+?)\s*$", r"*\1*", s, flags=re.MULTILINE)

    # **bold** → *bold*  (Telegram legacy MD uses single asterisks)
    s = re.sub(r"\*\*([^\*\n]+?)\*\*", r"*\1*", s)

    # Bullet `- text` → `• text`
    s = re.sub(r"^[\s]{0,4}[-*+]\s+", "• ", s, flags=re.MULTILINE)

    # Horizontal rules → simple dashed line
    s = re.sub(r"^[-=_]{3,}\s*$", "─" * 20, s, flags=re.MULTILINE)

    return s

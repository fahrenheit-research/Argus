"""File delivery — send workspace files to the active surface.

When a tool (scribe_docx, ledger_xlsx, etc.) creates a file in
~/argus-workspace, this helper automatically delivers it:

  • In Telegram: sends it as a document attachment via bot.send_document()
                 so the user gets a downloadable file bubble in chat.
  • In CLI:      prints the path; the user can open it normally.

Usage (at the end of any document-creation tool handler):

    from argus.tools.file_delivery import deliver_file
    return await deliver_file(path, title="Q4 Report")
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# Icons for different file types shown in Telegram caption
_ICONS = {
    ".docx": "📄",
    ".doc":  "📄",
    ".pdf":  "📋",
    ".xlsx": "📊",
    ".xls":  "📊",
    ".csv":  "📑",
    ".txt":  "📝",
    ".pptx": "📊",
    ".zip":  "📦",
}


async def deliver_file(path: Path, *,
                        title: str = "",
                        extra: str = "") -> str:
    """Deliver `path` to the current surface.

    Telegram  → sends `bot.send_document()`; returns "OK: sent <name> to chat"
    CLI       → returns "OK: wrote <path> (<size>)"

    `title`  — shown in the Telegram caption
    `extra`  — any extra text appended to the result string
    """
    if not path.exists():
        return f"ERROR: file not found: {path}"

    size    = path.stat().st_size
    icon    = _ICONS.get(path.suffix.lower(), "📎")
    size_kb = f"{size / 1024:.0f} KB"

    # ── Telegram: send as document attachment ───────────────────────────
    from argus.platform_context import get_platform_context
    pctx = get_platform_context()
    if pctx and pctx.supports_file_send:
        caption = (f"{icon} *{path.name}* ({size_kb})"
                   + (f"\n{title}" if title else ""))
        try:
            with path.open("rb") as fh:
                await pctx.bot.send_document(
                    chat_id=pctx.chat_id,
                    document=fh,
                    filename=path.name,
                    caption=caption,
                    parse_mode="MARKDOWN",
                )
            return (f"OK: sent {path.name} to chat ({size_kb})"
                    + (f" — {extra}" if extra else ""))
        except Exception as e:  # noqa: BLE001
            # Fall through and return the path if Telegram send fails
            return (f"OK: wrote {path} ({size_kb}) "
                    f"[Telegram send failed: {type(e).__name__}: {e}]"
                    + (f" — {extra}" if extra else ""))

    # ── CLI: return path ─────────────────────────────────────────────────
    return (f"OK: wrote {path} ({size_kb})"
            + (f" — {extra}" if extra else ""))

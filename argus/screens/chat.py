"""`argus chat` — the interactive REPL.

Visual design:
  - User input:    gradient animation between Electric Magenta #FF38D1 ↔ Ice Cyan #42E8F5
  - LLM response:  warm beige #F5E6C8
  - Tool calls:    italic Ice Cyan with Acid Gold lightning bolt
  - ⟨◇⟩ spinner:  pulsing while the LLM thinks (before first token)
  - Sound:         soft chime when response completes
  - Status bar:    persistent bottom toolbar (Hermes-style)
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Iterator

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import DynamicStyle, Style as _PTKStyle
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from argus import config as _config
from argus import paths
from argus.banner import print_banner, show_goodbye
from argus.data.providers import get_provider as _get_pinfo
from argus.glyph import inline_prompt_glyph
from argus.screens._common import err_panel, hint
from argus.slash import EXIT, ChatState, dispatch
from argus.stub.llm import FauxProvider
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA
from argus.theme import console as new_console

# ── Brand colors (exact hex from PRD) ────────────────────────────────────────
_MAGENTA_RGB = (0xFF, 0x38, 0xD1)   # Electric Magenta
_CYAN_RGB    = (0x42, 0xE8, 0xF5)   # Ice Cyan

# Beige for LLM response text — warm, readable, distinctly different from input
BEIGE = "#F5E6C8"

_SLASH = [
    "/help", "/me", "/talk", "/voice", "/listen",
    "/new", "/clear", "/clearmemory", "/restart", "/model", "/tools",
    "/memory", "/memory add", "/skills", "/sessions", "/resume",
    "/status", "/report", "/stop", "/approve", "/attach", "/brain", "/orchestrate",
    "/setup", "/key", "/telegram", "/gateway", "/config", "/doctor",
    "/connect", "/reset", "/quit", "/exit",
]


# Natural-language report triggers — typed as "Argus Report" / "argus report",
# this prints a full status report instead of going through the LLM.
_REPORT_TRIGGERS: frozenset[str] = frozenset({
    "argus report", "argus status", "report", "argus full report",
    "show status", "show report", "system status",
    "/report", "/status report",
})


# Natural-language "clear memory" triggers — wipes MEMORY.md + USER.md only,
# never touches .env / OAuth tokens / connectors.
_CLEAR_MEMORY_TRIGGERS: frozenset[str] = frozenset({
    "clear memory", "wipe memory", "reset memory", "erase memory",
    "/clearmemory", "/clear memory", "/clear-memory", "/memory clear",
    "forget everything", "argus forget", "argus clear memory",
})

# Natural-language "Argus Talk" triggers — switch on the mic, listen for
# "Hey Argus", run voice loop until the user says "End Argus" or Ctrl-C.
_TALK_TRIGGERS: frozenset[str] = frozenset({
    "argus talk", "argus, talk", "argus listen", "argus, listen",
    "talk to argus", "voice mode", "voice on", "argus voice",
    "/talk", "/voice", "/listen",
    "wake up argus", "argus wake up",
    "hey argus talk to me",
})


# Natural-language "Argus Me" triggers — opens the business-onboarding wizard.
# Lets the user paste a new website any time and Argus re-learns from scratch.
_ME_TRIGGERS: frozenset[str] = frozenset({
    "argus me", "me", "/me",
    "learn my business", "argus learn my business",
    "argus, learn my business",
    "remember my business", "argus remember my business",
    "scan my business", "argus scan my business",
    "rebrand", "argus rebrand",
    "new business", "argus new business",
    "update my business", "argus update my business",
    "change business", "argus change business",
    "switch business", "argus switch business",
    "my business", "tell argus about my business",
})

# Natural-language exit phrases — intercepted before the LLM sees them.
# Case-insensitive. User types any of these → same clean exit as /quit.
_EXIT_PHRASES: frozenset[str] = frozenset({
    "exit", "exit argus", "quit argus", "quit",
    "bye", "bye argus", "goodbye", "goodbye argus",
    "bye bye", "farewell", "see ya", "see you",
    "stop argus", "close argus", "shut down", "shutdown",
    "close", "stop", "end", "end session",
    ":q", ":q!", "q", "q!",            # vim habits
    "exit()", "quit()",                # Python habits
})


def _is_exit_phrase(text: str) -> bool:
    """Return True if the user typed a natural-language exit phrase."""
    return text.strip().lower() in _EXIT_PHRASES


# ── Brain / memory trigger ────────────────────────────────────────────────────

_BRAIN_TRIGGERS = frozenset({
    "brain", "argus brain", "argus memory", "show memory", "memory dashboard",
    "memory status", "memory usage", "brain status", "show brain",
    "how much memory", "memory stats",
})


def _set_terminal_font_size(size: int = 15) -> None:
    """Set the font size of the current Terminal.app window to `size` pt.

    Called once at startup so the CLI is displayed at 130% (15pt vs default
    13pt) without the user having to manually adjust settings.
    Silent no-op on non-macOS, non-TTY, or any error.
    """
    import sys, os, shutil, subprocess, textwrap
    if not sys.stdout.isatty():
        return
    if not shutil.which("osascript"):
        return
    script = textwrap.dedent(f"""\
        tell application "Terminal"
            try
                tell front window
                    tell selected tab
                        set font size to {size}
                    end tell
                    set bounds to {{40, 44, 1300, 844}}
                end tell
            end try
        end tell
    """)
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, timeout=3,
        )
    except Exception:
        pass


def _show_business_welcome(console: Console) -> str | None:
    """Render the appropriate welcome for this boot.

    Returns:
        str — a pre-filled first message to inject into the chat (when the
              user picks a use case from the first-run briefing).
        None — no auto-message; go straight to the prompt.
    """
    from argus import business as _biz

    profile = _biz.load()
    if not profile:
        return None   # user skipped onboarding — nothing to render

    if _biz.is_first_run(profile):
        return _show_first_run_briefing(console, profile)
    _show_returning_welcome(console, profile)
    return None


def _show_returning_welcome(console: Console, profile: dict) -> None:
    """Returning-user boot — total silence by design.

    Users explicitly asked to keep the load clean for daily use. The
    intelligence briefing only fires on first-run; after that, the chat
    prompt is the only thing they should see. The business profile is
    still available via:
      • `argus business` / `argus me` (CLI)
      • `Argus Report` / `/report` (chat)
    """
    return


def _show_first_run_briefing(console: Console, profile: dict) -> str | None:
    """First-time briefing — compact, fits in one terminal screen (~24 rows),
    free-text input (no forced selection).

    Layout (no scroll on a standard 80×24 terminal):

      ⟨◇⟩  INTELLIGENCE BRIEFING                                 first session
      ──────────────────────────────────────────────────────────────────────
        {name}  ·  {url}  ·  {tone}
        "{tagline}"
        {what_they_do — 2-line wrap max}
        Ships: {products joined with ·}   |   Serves: {audience}
      ──────────────────────────────────────────────────────────────────────
       1.  {title}                                          {effort} · {cat}
           ▸ first: {first_step shorthand}
       2.  {title}                                          ...
           ▸ first: ...
       3.  {title}                                          ...
           ▸ first: ...
      ──────────────────────────────────────────────────────────────────────
        ▸ type 1, 2, or 3 to run that move  ·  or type ANY other prompt
        ▸ press Enter to skip and go straight to chat

    The user picks by typing a digit OR types anything else (which becomes
    their first message). Nothing is compulsory.
    """
    import asyncio as _aio
    from argus import business as _biz
    from rich.text import Text as _T

    name = profile.get("name") or "your business"
    url  = profile.get("url") or ""
    tone = profile.get("tone") or "professional"
    tagline = (profile.get("tagline") or "").strip()
    what_they_do = (profile.get("what_they_do") or "").strip()
    products = profile.get("products", []) or []
    audience = (profile.get("audience") or "").strip()

    # Get terminal width for clean wrapping
    try:
        import shutil as _shutil
        term_w = max(60, min(_shutil.get_terminal_size().columns, 100))
    except Exception:
        term_w = 80
    rule = "─" * (term_w - 2)

    # ── Header (1 row) ───────────────────────────────────────────────
    console.print()
    hero = _T()
    hero.append("  ⟨◇⟩  ", style=GOLD)
    hero.append("INTELLIGENCE BRIEFING", style=f"bold {MAGENTA}")
    pad = term_w - 26 - len("first session") - 2
    hero.append(" " * max(2, pad), style=DIM)
    hero.append("first session", style=DIM)
    console.print(hero)
    console.print(_T(f"  {rule}", style=DIM))

    # ── Profile (3-5 rows) ───────────────────────────────────────────
    line1 = _T()
    line1.append("  ", style=DIM)
    line1.append(name, style=f"bold {GOLD}")
    line1.append("  ·  ", style=DIM)
    line1.append(url, style=CYAN)
    line1.append("  ·  ", style=DIM)
    line1.append(tone, style=BEIGE)
    console.print(line1)

    if tagline:
        line2 = _T()
        line2.append("  ", style=DIM)
        line2.append(f'"{tagline}"', style=f"italic {BEIGE}")
        console.print(line2)

    if what_they_do:
        # Wrap manually to avoid Rich's panel overhead; keep to ≤ 2 lines
        max_chars = (term_w - 4) * 2
        snippet = what_they_do[:max_chars]
        line3 = _T()
        line3.append("  ", style=DIM)
        line3.append(snippet, style=BEIGE)
        console.print(line3)

    if products or audience:
        line4 = _T()
        line4.append("  ", style=DIM)
        if products:
            line4.append("Ships: ", style=DIM)
            line4.append(" · ".join(str(p)[:30] for p in products[:4]), style=BEIGE)
        if products and audience:
            line4.append("   |   ", style=DIM)
        if audience:
            line4.append("Serves: ", style=DIM)
            line4.append(audience[:60], style=BEIGE)
        console.print(line4)

    console.print(_T(f"  {rule}", style=DIM))

    # ── Use cases (LLM call — ~3-10s) ────────────────────────────────
    spinner = _T("  ⟨◇⟩  drafting three moves you can run right now…", style=GOLD)
    console.print(spinner)
    try:
        use_cases = _aio.run(_biz.generate_first_run_use_cases(profile))
    except Exception:
        use_cases = _biz._fallback_use_cases(profile)
    if not use_cases:
        use_cases = _biz._fallback_use_cases(profile)

    # Erase the spinner line (cursor up + clear line)
    try:
        import sys as _sys
        _sys.stdout.write("\x1b[1A\x1b[2K")
        _sys.stdout.flush()
    except Exception:
        pass

    # ── Render compact use cards (2 lines each, 6 lines total) ───────
    for i, uc in enumerate(use_cases, 1):
        cat = (uc.get("category") or "automation").lower()
        cat_color = {"research": CYAN, "content": GOLD, "outreach": MAGENTA,
                     "analysis": CYAN, "automation": GOLD}.get(cat, CYAN)
        title = uc.get("title", "(no title)")
        effort = uc.get("effort", "?")
        first = uc.get("first_step", "")

        # Title line: "  1.  Title                       <effort · category>"
        title_line = _T()
        title_line.append(f"   {i}.  ", style=f"bold {MAGENTA}")
        max_title_w = term_w - 6 - len(effort) - len(cat) - 7
        t_display = title[:max_title_w]
        title_line.append(t_display, style=f"bold {BEIGE}")
        right = f"{effort}  ·  {cat}"
        pad = max(2, term_w - 6 - len(t_display) - len(right))
        title_line.append(" " * pad, style=DIM)
        title_line.append(right, style=cat_color)
        console.print(title_line)

        # First-step line: "      ▸ first: <step truncated to fit>"
        first_max = term_w - 16
        first_short = first if len(first) <= first_max else first[: first_max - 1] + "…"
        step_line = _T()
        step_line.append("       ▸ first: ", style=DIM)
        step_line.append(first_short, style=CYAN)
        console.print(step_line)

    console.print(_T(f"  {rule}", style=DIM))

    # ── Free-text input (NOT a forced picker) ────────────────────────
    hint_line = _T()
    hint_line.append("   ▸ ", style=GOLD)
    hint_line.append("type ", style=DIM)
    hint_line.append("1", style=f"bold {CYAN}")
    hint_line.append(", ", style=DIM)
    hint_line.append("2", style=f"bold {CYAN}")
    hint_line.append(", or ", style=DIM)
    hint_line.append("3", style=f"bold {CYAN}")
    hint_line.append(" to run that move  ·  or just type any prompt  ·  Enter to skip",
                     style=DIM)
    console.print(hint_line)
    console.print()

    # Use prompt_toolkit directly for the briefing input — single-line,
    # accepts ANY string including bare digits. NOT a forced picker.
    try:
        from prompt_toolkit import prompt as _pt_prompt
        from prompt_toolkit.formatted_text import FormattedText as _FT
        user_input = _pt_prompt(_FT([(f"fg:{MAGENTA} bold", "   › ")]))
    except (KeyboardInterrupt, EOFError):
        _biz.mark_first_run_done(use_case_selected=None)
        return None

    user_input = (user_input or "").strip()

    # ── Resolve ──────────────────────────────────────────────────────
    chosen_uc: dict | None = None
    prefill: str | None = None

    if not user_input:
        # Skipped — go straight to chat
        _biz.mark_first_run_done(use_case_selected=None)
        return None

    if user_input in ("1", "2", "3") and int(user_input) <= len(use_cases):
        chosen_uc = use_cases[int(user_input) - 1]
        prefill = _compose_execute_prompt(chosen_uc, profile)
    else:
        # User typed their own prompt — honour it
        prefill = user_input

    _biz.mark_first_run_done(use_case_selected=chosen_uc)

    # ── Launch flash ─────────────────────────────────────────────────
    console.print()
    flash = _T()
    flash.append("  ⟨◇⟩  ", style=GOLD)
    if chosen_uc:
        flash.append("On it. ", style=f"bold {MAGENTA}")
        flash.append("Running: ", style=DIM)
        flash.append(chosen_uc["title"], style=f"bold {BEIGE}")
    else:
        flash.append("Watching.", style=f"bold {MAGENTA}")
    console.print(flash)
    return prefill


def _compose_execute_prompt(uc: dict, profile: dict) -> str:
    """Turn a use-case dict into the synthetic user-message that ARGUS will
    process as if the user had typed it. Crafted to push the agent toward
    real tool execution, not just a summary.
    """
    name = profile.get("name") or "the business"
    url  = profile.get("url") or ""
    return (
        f"Execute this for {name}: {uc['title']}.\n\n"
        f"Why it matters: {uc['why']}\n\n"
        f"Start with this first step: {uc['first_step']}\n\n"
        f"Context you have: company at {url}, "
        f"products: {', '.join(profile.get('products', [])[:4]) or 'see profile'}, "
        f"audience: {profile.get('audience', 'see profile')}.\n\n"
        f"Do the work now. Use real tools. Don't ask permission — execute and "
        f"report what you found. Aim for a useful artefact in under "
        f"{uc.get('effort', 'a few minutes')}."
    )


def _detect_connector_intent(text: str) -> str | None:
    """Return a connector id if the text is a 'connect X' request, else None."""
    try:
        from argus.connectors.registry import detect_connect_intent
        c = detect_connect_intent(text)
        return c.id if c else None
    except Exception:
        return None


_LONG_PASTE_THRESHOLD = 2_000    # chars beyond which we save to a file


def _handle_long_paste(text: str, console: Console, state: ChatState) -> str:
    """If the text is very long, save it to ~/argus-workspace and return a
    compact reference so the LLM can read the file via read_file tool.

    Returns the (possibly modified) text, or "" to skip the turn.
    """
    if len(text) <= _LONG_PASTE_THRESHOLD:
        return text

    from pathlib import Path
    import time as _time

    ws = Path.home() / "argus-workspace"
    ws.mkdir(exist_ok=True)
    stamp = int(_time.time())
    fname = f"paste_{stamp}.txt"
    fpath = ws / fname
    fpath.write_text(text)

    kb = len(text.encode()) / 1024
    from rich.text import Text as _T
    body = _T()
    body.append(f"\n  📄 Long paste ({kb:.1f} KB, {len(text):,} chars) → ", style=GOLD)
    body.append(f"~/argus-workspace/{fname}", style=CYAN)
    body.append("\n  Ask your question and ARGUS will read the file automatically.\n", style=DIM)
    console.print(body)

    # Inject the file attachment so the agent sees it on next turn
    state.attached_files.append((fname, text[:500] + "\n…(truncated, full file available)", str(fpath)))

    # Return a concise proxy message — user still needs to ask their question
    return f"[Pasted a {kb:.1f} KB text file: ~/argus-workspace/{fname}] Please read and answer."


def _is_brain_query(text: str) -> bool:
    t = text.strip().lower()
    if t in _BRAIN_TRIGGERS:
        return True
    # "show me the brain" / "check memory" etc.
    return any(trigger in t for trigger in ("argus brain", "argus memory", "brain status"))


# ── Paste / script / MCP / token detection ───────────────────────────────────


def _is_paste(text: str) -> bool:
    """Heuristic: does this look like pasted config / script content?"""
    from argus.tools.mcp_setup import classify_paste
    return classify_paste(text) is not None


def _handle_paste(text: str, console: Console, state: ChatState) -> bool:
    """Detect, preview, and optionally apply pasted config.

    Returns True if the paste was fully handled (skip sending to LLM).
    """
    from argus.tools.mcp_setup import (
        classify_paste,
        handle_api_token,
        handle_env_vars,
        handle_mcp_config,
        handle_shell_script,
        persist_env_vars,
    )
    from argus import picker
    from rich.text import Text as _Text

    kind = classify_paste(text)
    if not kind:
        return False

    if kind == "api_token":
        provider_id = handle_api_token(text.strip(), console)
        if provider_id:
            if picker.confirm(f"Save this key as your {provider_id} API key?"):
                from argus.screens.key import run as key_run
                key_run(provider_id, text.strip())
        return True

    if kind == "env_vars":
        recognised = handle_env_vars(text, console)
        if recognised:
            if picker.confirm(f"Save {len(recognised)} key(s) to ~/.argus/.env?"):
                persist_env_vars(recognised, console)
        return True

    if kind == "mcp_config":
        handle_mcp_config(text, console)
        return True

    if kind == "shell_script":
        handle_shell_script(text, console)
        return True

    if kind == "mcp_config":
        handle_mcp_config(text, console)
        return True

    if kind in ("json_config", "env_vars_json"):
        # Check if it's a Google or Microsoft auth JSON
        try:
            from argus.connectors.registry import detect_auth_json, run_connect_wizard
            auth_kind = detect_auth_json(text)
            if auth_kind == "google_auth":
                from rich.text import Text as _T
                from argus.theme import GOLD
                console.print(_T("\n  Detected Google auth JSON — starting Gmail/Google connector setup…", style=f"bold {GOLD}"))
                # Pre-populate credentials and jump to oauth step
                import json as _json
                try:
                    data = _json.loads(text)
                    installed = data.get("installed") or data.get("web") or data
                    from argus.connectors.gmail import GmailConnector
                    gc = GmailConnector()
                    gc.save_credentials({
                        "client_id":     installed.get("client_id", ""),
                        "client_secret": installed.get("client_secret", ""),
                        "type":          "oauth2",
                    })
                    run_connect_wizard("gmail", console)
                except Exception:
                    pass
                return True
            elif auth_kind == "microsoft_auth":
                from rich.text import Text as _T
                console.print(_T("\n  Detected Microsoft auth JSON — starting Outlook connector setup…", style=f"bold {GOLD}"))
                run_connect_wizard("outlook", console)
                return True
        except Exception:
            pass
        # Unknown JSON → fall through to LLM
        return False

    return False


# ── Auto-attach dragged / pasted file paths ───────────────────────────────

import re as _re
_FILE_PATH_RE = _re.compile(
    r"""^['"]?(/[^\s'"]+|~/[^\s'"]+|[A-Za-z]:\\[^\s'"]+)['"]?$"""
)


def _maybe_auto_attach(line: str, console: Console, state: ChatState) -> str:
    """If the user pasted or dragged a bare file path, auto-attach it.

    Returns the (possibly unchanged) line. If the line is ONLY a path with
    no other text, we attach and return "" so the REPL waits for a follow-up
    question. If the path is embedded in a sentence, we attach and return
    the sentence with the path stripped, so the rest goes to the LLM.
    """
    from pathlib import Path
    import os

    stripped = line.strip().strip("'\"")

    # Case 1: entire input is a bare file path → auto-attach + wait
    m = _FILE_PATH_RE.match(line.strip())
    if m:
        candidate = Path(os.path.expanduser(stripped)).resolve()
        if candidate.exists() and candidate.is_file():
            from argus.slash import _attach
            _attach(stripped, console, state)
            return ""   # REPL will loop and ask user for their question

    return line


# ── Gradient animation for user input ────────────────────────────────────────

def _lerp_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> str:
    r = round(a[0] + (b[0] - a[0]) * t)
    g = round(a[1] + (b[1] - a[1]) * t)
    bv = round(a[2] + (b[2] - a[2]) * t)
    return f"#{r:02X}{g:02X}{bv:02X}"


def _gradient_input_style() -> _PTKStyle:
    """Returns a DynamicStyle that cycles the user's typed text
    smoothly between Electric Magenta and Ice Cyan (~1 full cycle per 4s)."""
    t = time.time()
    ratio = (math.sin(t * math.pi * 0.5) + 1) / 2   # 0 → 1, period ~4s
    color = _lerp_rgb(_MAGENTA_RGB, _CYAN_RGB, ratio)
    return _PTKStyle.from_dict({
        "":                    color,           # user's typed text (gradient)
        "input-text":          color,           # explicit class for lexer plain text
        "url":                 "#42E8F5 underline",   # URLs/emails — Ice Cyan underlined
        "bottom-toolbar":      "noinherit",     # toolbar uses its own colours
        "completion-menu":     f"bg:#1A1A1A {CYAN}",
        "completion-menu.completion.current": f"bg:{MAGENTA} #000000 bold",
    })


# ── ⟨◇⟩ thinking animation ───────────────────────────────────────────────────

_MAGENTA_ANSI = "\033[38;2;255;56;209m"
_GOLD_ANSI    = "\033[38;2;255;194;71m"
_CYAN_ANSI    = "\033[38;2;66;232;245m"
_DIM_ANSI     = "\033[2m"
_RESET_ANSI   = "\033[0m"

_DIAMONDS  = ["◇", "◈", "◆", "◈"]
_DOT_TRAIL = ["", "·", "··", "···", "··", "·"]


def _run_thinking_animation(stop: threading.Event, prefix: str = "    ") -> None:
    """Animate ⟨◇⟩ pulsing while the LLM processes. Runs in a daemon thread.

    Uses raw ANSI escapes (not Rich) to avoid console-write conflicts with
    the main thread. Writes to stdout, clears itself when done.
    """
    i = 0
    while not stop.wait(0.16):          # ~6 fps — smooth but not CPU-hungry
        d = _DIAMONDS[i % len(_DIAMONDS)]
        dots = _DOT_TRAIL[i % len(_DOT_TRAIL)]
        # Build the animation line with ANSI colors
        line = (
            f"\r{prefix}"
            f"{_MAGENTA_ANSI}⟨{_RESET_ANSI}"
            f"{_GOLD_ANSI}{d}{_RESET_ANSI}"
            f"{_MAGENTA_ANSI}⟩{_RESET_ANSI}"
            f"  {_DIM_ANSI}thinking{dots}{_RESET_ANSI}"
            "          "            # trailing spaces to erase previous longer line
        )
        sys.stdout.write(line)
        sys.stdout.flush()
        i += 1

    # Erase the animation line completely before the real response renders
    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()


# ── Sound ─────────────────────────────────────────────────────────────────────

def _play_complete_sound() -> None:
    """Play a soft chime when the LLM finishes. Non-blocking; never crashes."""
    import subprocess
    try:
        if sys.platform == "darwin":
            # macOS: Glass is the cleanest system chime
            subprocess.Popen(
                ["afplay", "/System/Library/Sounds/Glass.aiff"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        # Linux: try PulseAudio then ALSA then terminal bell
        for cmd in [
            ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
            ["aplay",  "-q", "/usr/share/sounds/alsa/Front_Center.wav"],
        ]:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except FileNotFoundError:
                continue
    except Exception:
        pass
    # Last resort: terminal bell
    sys.stdout.write("\a")
    sys.stdout.flush()


# ── REPL entry point ──────────────────────────────────────────────────────────

def run(
    *,
    one_shot: str | None = None,
    quiet: bool = False,
    provider: str | None = None,
    model: str | None = None,
    toolsets: list[str] | None = None,
    resume: str | None = None,
) -> None:
    console = new_console()

    # Load config to get the real default provider/model
    _cfg_now = _config.load()
    state = ChatState(
        provider=provider or _cfg_now.agent.default_provider,
        model=model or _cfg_now.agent.default_model,
        session_title=("resumed: " + resume) if resume else "fresh session",
    )

    if one_shot is not None:
        _one_shot(console, state, one_shot, quiet=quiet)
        return

    # Set main CLI terminal to 15pt font (130% of 13pt default) for crisp
    # readable text. Tries osascript on macOS; silently skips elsewhere.
    _set_terminal_font_size(15)

    # Boot splash — cool retro animation w/ brand colors before the banner.
    # No-op in non-TTY, under ARGUS_NO_SPLASH=1, or when --quiet is passed.
    from argus.splash import play_splash
    play_splash(quiet=quiet)

    print_banner(console, provider=state.provider, model=state.model)
    if toolsets:
        hint(console, f"toolsets restricted to: {', '.join(toolsets)}")
    if resume:
        hint(console, f"resumed: {resume}")

    # ── Auto-start background services (Telegram gateway + MCP) ──────
    # Runs immediately after the banner so status lines appear before
    # the 150-px spacer. Total overhead: < 100 ms (local PID probes only).
    try:
        from argus.services.autostart import run_autostart
        run_autostart(console)
    except Exception:
        pass   # never crash the CLI over a background service

    # ── Personalised business welcome ────────────────────────────────
    # FIRST RUN: rich intelligence briefing + 3 use-case picker; whatever
    #            the user picks becomes their first chat turn (auto-executed).
    # RETURNS:   subtle one-line reminder of what we're watching.
    # NO PROFILE: nothing rendered.
    _prefill_first_message: str | None = None
    try:
        _prefill_first_message = _show_business_welcome(console)
    except Exception:
        pass

    # 150 px of breathing room between the banner and the first prompt.
    # At 16pt font (~21px line-height) that is 7 blank lines.
    console.print("\n" * 5, end="")

    history = InMemoryHistory()
    completer = WordCompleter(_SLASH, ignore_case=True, sentence=True)

    def _bottom_toolbar() -> FormattedText:
        ctx = state.tokens_in + state.tokens_out
        ctx_str = f"{ctx/1000:.1f}K" if ctx > 999 else str(ctx) if ctx else "--"
        bar = "[░░░░░░░░░]" if ctx == 0 else _bar_for(ctx, ctx_max=128_000)
        pct = f"{min(99, (ctx * 100) // 128_000)}%" if ctx else "0%"
        ttft = f"{getattr(state, 'last_ttft_ms', 0) / 1000:.1f}s" if getattr(state, "last_ttft_ms", 0) else "--"

        # Voice status (reads from the shared VoiceState singleton)
        voice_segment: list[tuple[str, str]] = []
        try:
            from argus.voice.listener import voice_state as _vs
            if _vs.active:
                vcolor_map = {
                    "asleep":    f"fg:{DIM}",
                    "arming":    f"fg:{CYAN}",
                    "listening": f"fg:{MAGENTA} bold",
                    "thinking":  f"fg:{GOLD} bold",
                    "speaking":  f"fg:{GOLD} bold",
                }
                glyph_map = {
                    "asleep": "⟨◇⟩", "arming": "⟨◈⟩", "listening": "⟨◈⟩",
                    "thinking": "⟨◆⟩", "speaking": "⟨◆⟩",
                }
                vc     = vcolor_map.get(_vs.status, f"fg:{DIM}")
                vglyph = glyph_map.get(_vs.status, "⟨◇⟩")
                vlabel = _vs.status.upper().ljust(9)
                voice_segment = [
                    (f"fg:{DIM}",     "  |  "),
                    (vc,              f"{vglyph} {vlabel}"),
                    (f"fg:{DIM}",     f"  {_vs.hint[:30]}"),
                ]
        except Exception:
            pass

        return FormattedText([
            ("",                   "\n"),
            (f"fg:{MAGENTA} bold", " ⟨◇⟩ "),
            (f"fg:{GOLD} bold",    state.model),
            (f"fg:{DIM}",         "  |  "),
            (f"fg:{CYAN}",        f"ctx {ctx_str}"),
            (f"fg:{DIM}",         f"  {bar} "),
            (f"fg:{CYAN}",        pct),
            (f"fg:{DIM}",         "  |  "),
            (f"fg:{CYAN}",        f"ttft {ttft}"),
            (f"fg:{DIM}",         "  |  "),
            (f"fg:{GOLD}",        f"${getattr(state, 'cost_usd', 0.0):.2f}"),
            *voice_segment,
        ])

    # Lexer that highlights URLs and email addresses in the input line
    # as the user types, using Ice Cyan — same style as in rendered output.
    from prompt_toolkit.lexers import Lexer as _Lexer
    from prompt_toolkit.document import Document as _Doc
    from prompt_toolkit.formatted_text import StyleAndTextTuples as _SAT

    class _URLLexer(_Lexer):
        """Highlight bare URLs and email addresses in Ice Cyan while typing."""
        import re as _re_cls
        _URL_PAT   = _re_cls.compile(r"https?://\S+", _re_cls.IGNORECASE)
        _EMAIL_PAT = _re_cls.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")

        def lex_document(self, doc: _Doc):
            text = doc.text

            def _get_line(lineno: int) -> _SAT:
                if lineno != 0:
                    return []
                spans: list[tuple[int, int, str]] = []   # (start, end, style)
                for pat in (self._URL_PAT, self._EMAIL_PAT):
                    for m in pat.finditer(text):
                        spans.append((m.start(), m.end(), "class:url"))
                spans.sort(key=lambda x: x[0])

                result: _SAT = []
                pos = 0
                for start, end, style in spans:
                    if start > pos:
                        result.append(("class:input-text", text[pos:start]))
                    result.append((style, text[start:end]))
                    pos = end
                if pos < len(text):
                    result.append(("class:input-text", text[pos:]))
                return result or [("class:input-text", text)]

            return _get_line

    session: PromptSession = PromptSession(
        history=history,
        completer=completer,
        complete_while_typing=False,
        multiline=False,
        bottom_toolbar=_bottom_toolbar,
        lexer=_URLLexer(),
        refresh_interval=0.05,
        style=DynamicStyle(_gradient_input_style),
        include_default_pygments_style=False,
    )

    while True:
        # First iteration only: if the user picked a use case during the
        # first-run briefing, inject it as the first user turn instead of
        # waiting at the prompt. Subsequent iterations always prompt.
        if _prefill_first_message:
            line = _prefill_first_message
            _prefill_first_message = None
            # Echo a stylised user-turn so the conversation looks coherent:
            # the LLM is about to see this message; the user should too.
            _print_user_turn(console,
                             f"⟨◇⟩ executing the selected use case…")
        else:
            try:
                line = session.prompt(
                    FormattedText([(f"fg:{MAGENTA} bold", "›  ")]),
                )
            except (KeyboardInterrupt, EOFError):
                show_goodbye(console)
                return

        line = line.strip()
        if not line:
            continue

        # ── Natural-language exit — checked BEFORE slash dispatch and LLM ──
        if _is_exit_phrase(line):
            _print_user_turn(console, line)
            show_goodbye(console)
            return

        # ── "Brain" / "Argus Memory" → show memory dashboard ─────────────
        if _is_brain_query(line):
            _print_user_turn(console, line)
            from argus.slash import _brain
            _brain("", console, state)
            continue

        # ── "Argus Report" → full status report ──────────────────────────
        if line.strip().lower() in _REPORT_TRIGGERS:
            _print_user_turn(console, line)
            import asyncio as _aio
            from argus.reports import build_status_report
            _cfg = _config.load()
            report = _aio.run(build_status_report(_cfg, format="terminal"))
            from rich.markdown import Markdown
            console.print(Markdown(report))
            continue

        # ── "Argus Talk" → voice daemon thread, prompt stays live ────────
        if line.strip().lower() in _TALK_TRIGGERS:
            _print_user_turn(console, line)
            try:
                from argus.voice.listener import start_voice_thread, voice_state as _vs
                from rich.text import Text as _T
                if _vs.active:
                    console.print(_T(
                        "  ⟨◇⟩  Voice already running. Say 'End Argus' to close.",
                        style=DIM))
                else:
                    # Runs in a daemon thread — this line returns immediately.
                    # The prompt stays fully live. Voice status shows in toolbar.
                    start_voice_thread(
                        wake_model="hey_argus",
                        stt_model="tiny.en",
                        speak=True,
                    )
                    console.print(_T(
                        "  ⟨◇⟩  Voice on.  "
                        "Say 'Hey Argus' to wake  ·  'End Argus' to close  ·  "
                        "you can still type below.",
                        style=DIM,
                    ))
            except Exception as e:  # noqa: BLE001
                from argus.screens._common import err_panel
                err_panel(console, f"voice mode failed to start: {e}",
                          sub="run `argus doctor` to check dependencies")
            continue

        # ── "Argus Me" → business onboarding (paste a new URL) ───────────
        if line.strip().lower() in _ME_TRIGGERS:
            _print_user_turn(console, line)
            from argus import business as _biz
            _biz.run_me_wizard(console)
            continue

        # ── "Clear memory" → wipe MEMORY.md + USER.md only ───────────────
        if line.strip().lower() in _CLEAR_MEMORY_TRIGGERS:
            _print_user_turn(console, line)
            wiped: list[str] = []
            for f in (paths.MEMORY_MD, paths.USER_MD):
                if f.exists():
                    f.unlink()
                    wiped.append(f.name)
            from rich.text import Text as _T
            msg = _T()
            msg.append("  ✓ ", style=GOLD)
            msg.append("Memory cleared", style=BEIGE)
            if wiped:
                msg.append(f" ({', '.join(wiped)})", style=DIM)
            msg.append(
                ".\n    API keys, OAuth tokens, and connectors are untouched.",
                style=DIM,
            )
            console.print(msg)
            continue

        # ── "Connect gmail / twitter / …" → connector wizard ──────────────
        connect_target = _detect_connector_intent(line)
        if connect_target:
            _print_user_turn(console, line)
            from argus.connectors.registry import run_connect_wizard
            run_connect_wizard(connect_target, console)
            continue

        # ── Long paste → save to file and inject as attachment ────────────
        line = _handle_long_paste(line, console, state)
        if not line:
            continue

        # ── Script / MCP / token / Google auth JSON paste detection ───────
        if _is_paste(line):
            _print_user_turn(console, line)
            if _handle_paste(line, console, state):
                continue   # paste was fully handled — don't send to LLM

        # ── Auto-detect dragged/pasted file paths ─────────────────────────
        line = _maybe_auto_attach(line, console, state)
        if not line:          # pure path with no trailing message → wait
            continue

        _print_user_turn(console, line)

        if line.startswith("/"):
            try:
                result = dispatch(line, console, state)
            except Exception as e:  # noqa: BLE001
                err_panel(console, f"slash command crashed: {type(e).__name__}: {e}")
                continue
            if result is EXIT:
                return
            continue

        _print_agent_header(console)
        try:
            _stream_reply(console, state, line)
        except KeyboardInterrupt:
            console.print(Text("\n  ⟨◇⟩ interrupted", style="argus.dim"))
        except Exception as e:  # noqa: BLE001
            err_panel(console, f"reply failed: {type(e).__name__}: {e}",
                      sub="Try again, or /quit.")
        console.print()  # blank line between turns


# ── Turn rendering ─────────────────────────────────────────────────────────────


def _bar_for(used: int, ctx_max: int) -> str:
    pct = min(1.0, used / max(1, ctx_max))
    filled = int(pct * 9)
    return "[" + "█" * filled + "░" * (9 - filled) + "]"


def _print_user_turn(console: Console, text: str) -> None:
    """Render user message as `●  text` — gold bullet, white text."""
    body = Text()
    body.append("●  ", style=f"bold {GOLD}")
    body.append(text, style="bold #FFFFFF")
    console.print(body)


def _print_agent_header(console: Console) -> None:
    """Magenta horizontal rule with `⟨◇⟩ ARGUS` label."""
    label = Text()
    label.append(" ")
    label.append("⟨", style=f"bold {MAGENTA}")
    label.append("◇", style=f"bold {GOLD}")
    label.append("⟩ ", style=f"bold {MAGENTA}")
    label.append("ARGUS", style=f"bold {FG}")
    label.append(" ")
    console.print(Rule(label, style=MAGENTA, align="left"))


def _stream_reply(console: Console, state: ChatState, prompt: str) -> None:
    """Route to real provider or loud error if no key.

    If the user has attached files this turn (via /attach), their content
    is prepended to the prompt before it reaches the LLM, then the
    attached_files list is cleared so files don't leak into future turns.
    """
    cfg = _config.load()
    pinfo = _get_pinfo(state.provider)
    have_key = pinfo.auth == "local" or _config.has_secret(pinfo.env_var, cfg)

    if not have_key:
        body = Text()
        body.append("  ⟨◇⟩  ", style="argus.bracket")
        body.append(f"no API key for {pinfo.label} ({pinfo.env_var})\n", style="argus.err_text")
        body.append("  Run: ", style="argus.fg")
        body.append(f"argus key {pinfo.id} <your-api-key>", style="argus.cyan")
        console.print(body)
        console.print()
        return

    # Prepend attached file contents to the prompt
    enriched_prompt = _inject_attachments(state, prompt)

    # ── Auto-swarm detection ──────────────────────────────────────────────
    # If the prompt contains multiple distinct tasks, fan out to the
    # Constellation immediately — no "run constellation" needed.
    try:
        from argus.swarm.auto import should_swarm
        routable, confidence, swarm_reason = should_swarm(enriched_prompt)
        if routable:
            _print_agent_header(console)
            console.print(Text(
                f"  ⟨◆⟩  Swarm activated ({confidence:.0%}) — {swarm_reason}\n"
                f"  Fanning out to the Constellation…",
                style=DIM,
            ))
            _auto_swarm_cli(console, state, cfg, enriched_prompt)
            return
    except Exception:
        pass   # fall through to normal turn on any error

    _stream_real(console, state, cfg, enriched_prompt)


def _auto_swarm_cli(console: Console, state: ChatState, cfg,
                     goal: str) -> None:
    """Run the Constellation and stream its progress in the CLI."""
    import asyncio
    from argus.swarm import Constellation, ConstellationResult
    from argus.swarm.events import (
        BudgetTick, RoleStarted, RoleFinished, RoleFailed, ClaimVerdict,
    )
    from argus.render import render_to_rich_text

    CYAN = "#42E8F5"; GOLD = "#FFC247"; MAG = "#FF38D1"; D = "#7A7A7A"

    async def _run():
        c = Constellation(goal=goal, cfg=cfg,
                           budget_tokens=30_000, timeout_seconds=180)
        shown_roles: set[str] = set()
        async for evt in c.stream():
            cls = type(evt).__name__
            if cls == "RoleStarted" and evt.role not in shown_roles:
                shown_roles.add(evt.role)
                t = Text()
                t.append(f"  ▸ {evt.role:<14} ", style=f"bold {MAG}")
                t.append(evt.description[:60], style=D)
                console.print(t)
            elif cls == "RoleFinished":
                t = Text()
                t.append(f"    ✓ {evt.role:<13} ", style=f"bold {GOLD}")
                t.append(f"{evt.elapsed_ms / 1000:.1f}s", style=D)
                console.print(t)
            elif cls == "RoleFailed":
                console.print(Text(f"    ✗ {evt.role} failed — medic retrying", style=f"italic {D}"))
            elif cls == "BudgetTick" and evt.active_roles > 0:
                pct = int(evt.spent_tokens / max(1, evt.total_tokens) * 100)
                console.print(Text(
                    f"  ⟨◆⟩  {evt.active_roles} active · {pct}% budget · {evt.elapsed_ms // 1000}s",
                    style=D), end="\r",
                )
        return c.result

    try:
        result = asyncio.run(_run())
    except Exception as e:
        err_panel(console, f"Constellation failed: {type(e).__name__}: {e}",
                  sub="Falling back — try the prompt again.")
        return

    console.print()
    console.print(Rule(Text(
        f" ⟨◇⟩ ARGUS  ·  {result.role_count} roles  ·  "
        f"{len(result.confirmed_outputs)} verified  ·  "
        f"{result.elapsed_ms // 1000}s ",
        style=f"bold {GOLD}",
    ), style=MAG))
    console.print()

    styled = render_to_rich_text(result.answer)
    console.print(styled)
    state.tokens_in  += result.tokens_used // 2
    state.tokens_out += result.tokens_used // 2


def _inject_attachments(state: ChatState, prompt: str) -> str:
    """Prepend any attached files to the user's message, then clear the queue."""
    if not state.attached_files:
        return prompt

    parts: list[str] = []
    for name, content, path in state.attached_files:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        _CODE_LANGS = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "sh": "bash", "yaml": "yaml", "yml": "yaml", "json": "json",
            "md": "markdown", "toml": "toml", "rs": "rust", "go": "go",
            "java": "java", "rb": "ruby", "swift": "swift",
            "cpp": "cpp", "c": "c", "html": "html", "css": "css", "sql": "sql",
        }
        lang = _CODE_LANGS.get(ext, "")
        fence = f"```{lang}" if lang else "```"
        size_kb = len(content.encode()) / 1024
        parts.append(
            f"[Attached file: **{name}** ({size_kb:.1f} KB)]\n"
            f"{fence}\n{content}\n```"
        )

    state.attached_files.clear()

    file_block = "\n\n".join(parts)
    return f"{file_block}\n\n---\n\n{prompt}"


def _stream_real(console: Console, state: ChatState, cfg, prompt: str) -> None:
    """Collect all agent events then render them.

    Shows a ⟨◇⟩ thinking animation while the LLM computes, then renders
    the response in beige, plays a completion sound, and updates the
    time-to-first-token for the status bar.
    """
    import asyncio

    from argus.loop import (
        Conversation,
        ErrorEvent,
        FinishEvent,
        TextEvent,
        ToolCallEvent,
        ToolResultEvent,
        run_turn,
    )

    convo: Conversation = getattr(state, "_convo", None) or Conversation()
    state._convo = convo  # type: ignore[attr-defined]

    turn_start = time.monotonic()
    all_events: list = []

    async def collect() -> None:
        async for evt in run_turn(
            cfg, convo, prompt, provider_id=state.provider, model=state.model
        ):
            all_events.append(evt)

    # ── ⟨◇⟩ thinking animation ── start before asyncio.run() ─────────────
    stop_thinking = threading.Event()
    thinking_thread = threading.Thread(
        target=_run_thinking_animation,
        args=(stop_thinking, "    "),
        daemon=True,
    )
    thinking_thread.start()

    interrupted = False
    try:
        asyncio.run(collect())
    except KeyboardInterrupt:
        interrupted = True
    except RuntimeError as e:
        all_events.append(type("E", (), {"message": str(e), "__class__": ErrorEvent})())
    finally:
        # Always stop the animation before printing anything
        stop_thinking.set()
        thinking_thread.join(timeout=0.5)

    if interrupted:
        console.print(Text("\n  ⟨◇⟩ interrupted", style="argus.dim"))
        console.print()
        return

    # ── Render collected events ────────────────────────────────────────────
    body = Text("    ")          # 4-space indent under the divider
    has_text = False
    first_token_at: float | None = None

    # Collect the full assistant message first so we can render it as
    # one styled markdown block (titles in brand colors, bold in magenta,
    # em-dashes stripped) instead of dribbling raw tokens that include
    # `**` syntax and `—` characters.
    _assistant_chunks: list[str] = []
    for evt in all_events:
        if isinstance(evt, TextEvent):
            if not has_text:
                first_token_at = time.monotonic()
                has_text = True
            _assistant_chunks.append(evt.text)
            # We DON'T append to body here — see post-loop render below.

        elif isinstance(evt, ToolCallEvent):
            args = dict(list((evt.arguments or {}).items())[:2])
            preview = ", ".join(f"{k}={v!r}" for k, v in args.items())
            line = Text()
            line.append("\n    ⚡ ", style=f"bold {GOLD}")
            line.append(evt.name, style=f"italic {CYAN}")
            if preview:
                line.append(f"({preview})", style=f"italic {DIM}")
            line.append("…", style=f"italic {CYAN}")
            body.append_text(line)

        elif isinstance(evt, ToolResultEvent):
            body.append_text(
                Text(f" → done in {evt.elapsed_ms / 1000:.1f}s\n    ", style=f"italic {DIM}")
            )

        elif isinstance(evt, FinishEvent):
            state.tokens_in += evt.tokens_in
            state.tokens_out += evt.tokens_out

        elif isinstance(evt, ErrorEvent):
            body.append_text(
                Text(f"\n    ✗ {evt.message}", style="argus.err_text")
            )

    # ── Render the assistant's prose as styled markdown (after tool lines) ──
    # The agent's raw output can include em-dashes and **markdown** syntax.
    # render_to_rich_text strips em-dashes + turns markdown into actual
    # styling (bold magenta, headers in brand colors, etc.) so the user
    # never sees `**` or `—` characters in the chat.
    if _assistant_chunks:
        from argus.render import render_to_rich_text
        styled = render_to_rich_text("".join(_assistant_chunks))
        body.append("\n    ")
        body.append_text(styled)

    console.print(body)
    console.print()

    if first_token_at is not None:
        state.last_ttft_ms = int((first_token_at - turn_start) * 1000)  # type: ignore[attr-defined]

    # ── Chime after response ───────────────────────────────────────────────
    if has_text:
        _play_complete_sound()


def _one_shot(console: Console, state: ChatState, prompt: str, *, quiet: bool) -> None:
    """One-shot mode — send one prompt, print the reply, exit."""
    import asyncio

    cfg = _config.load()
    pinfo = _get_pinfo(state.provider)
    have_key = pinfo.auth == "local" or _config.has_secret(pinfo.env_var, cfg)

    if not have_key:
        msg = f"no API key for {pinfo.label} ({pinfo.env_var})\nFix: argus key {pinfo.id} <key>"
        (print if quiet else lambda m: err_panel(console, m))(msg)
        return

    if not quiet:
        print_banner(console, provider=state.provider, model=state.model)

    from argus.loop import (
        Conversation, ErrorEvent, FinishEvent, TextEvent,
        ToolCallEvent, ToolResultEvent, run_turn,
    )

    convo = Conversation()

    # Buffer the full assistant reply so we can render it once with brand
    # styling at the end — instead of dribbling raw `**bold**` + em-dash
    # tokens onto the terminal one-by-one.
    _buf: list[str] = []

    async def drive() -> None:
        async for evt in run_turn(cfg, convo, prompt,
                                   provider_id=state.provider, model=state.model):
            if isinstance(evt, TextEvent):
                _buf.append(evt.text)
                if quiet:
                    # Quiet mode = scriptable output; strip styling but still
                    # scrub em-dashes so downstream tooling stays clean.
                    from argus.render import clean_text
                    print(clean_text(evt.text), end="", flush=True)
            elif isinstance(evt, ToolCallEvent) and not quiet:
                console.print(Text(f"\n  ⚡ {evt.name}…", style=f"italic {CYAN}"))
            elif isinstance(evt, ToolResultEvent) and not quiet:
                console.print(Text(f"  done in {evt.elapsed_ms/1000:.1f}s", style=f"italic {DIM}"))
            elif isinstance(evt, ErrorEvent):
                msg = f"\n✗ {evt.message}"
                (print if quiet else console.print)(msg)
            elif isinstance(evt, FinishEvent):
                state.tokens_in += evt.tokens_in
                state.tokens_out += evt.tokens_out

    try:
        asyncio.run(drive())
    except Exception as e:  # noqa: BLE001
        (print if quiet else console.print)(f"ERROR: {e}")

    # ── Interactive one-shot: render the buffered text with brand styling ──
    if _buf and not quiet:
        from argus.render import render_to_rich_text
        console.print(render_to_rich_text("".join(_buf)))

    (print if quiet else lambda: console.print())()
    if not quiet:
        _play_complete_sound()

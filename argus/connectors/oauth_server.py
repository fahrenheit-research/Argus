"""Local OAuth2 callback server — with VPS-compatible manual-paste fallback.

On a desktop machine (browser available), spins up a temporary HTTP server
on localhost:55155 to catch the OAuth redirect automatically.

On a headless VPS (no browser / no display), falls back to a **manual mode**:
the user copies the auth URL, opens it on their local machine, completes the
OAuth flow, then pastes the full callback URL back into the ARGUS CLI. The
code is extracted from the pasted URL — no localhost server needed.

Compatible with Google, Microsoft, LinkedIn, Twitter OAuth flows — all
support localhost redirect URIs (the redirect still goes to localhost on the
user's browser; they just paste the resulting URL back).
"""

from __future__ import annotations

import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

REDIRECT_URI = "http://localhost:55155/callback"
_PORT = 55155


_SUCCESS_PAGE = """\
<!DOCTYPE html>
<html>
<head>
<title>ARGUS — Connected</title>
<style>
  body { background: #050505; color: #E6E6E6; font-family: monospace;
         display: flex; align-items: center; justify-content: center;
         height: 100vh; margin: 0; flex-direction: column; }
  .glyph { font-size: 3rem; color: #FF38D1; }
  .title { color: #FFC247; font-size: 1.5rem; margin: 1rem 0; }
  .msg   { color: #42E8F5; }
</style>
</head>
<body>
  <div class="glyph">⟨◇⟩</div>
  <div class="title">ARGUS — Connected</div>
  <div class="msg">You can close this tab and return to the terminal.</div>
</body>
</html>
"""

_ERROR_PAGE = """\
<!DOCTYPE html>
<html>
<head><title>ARGUS — Auth Error</title>
<style>body{background:#050505;color:#FF5C5C;font-family:monospace;
  display:flex;align-items:center;justify-content:center;height:100vh;
  flex-direction:column;}</style></head>
<body>
  <div style="font-size:3rem;color:#FF38D1">⟨◇⟩</div>
  <div style="color:#FF5C5C;font-size:1.2rem;margin:1rem">Auth error — check the terminal</div>
</body>
</html>
"""


def _is_headless() -> bool:
    """Detect if we're running on a headless server (no display / no browser).

    Checks:
      - DISPLAY env var (Linux X11)
      - WAYLAND_DISPLAY env var (Linux Wayland)
      - SSH_CONNECTION env var (SSH session → likely headless)
      - ARGUS_HEADLESS env var (explicit override)
      - macOS always has a display (unless SSH'd in)
    """
    # Explicit override: user can force headless mode
    if os.environ.get("ARGUS_HEADLESS", "").lower() in ("1", "true", "yes"):
        return True

    # If we're in an SSH session, assume headless
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return True

    # Linux: check for display server
    import sys
    if sys.platform == "linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            return True

    return False


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]

        self.server._result = {"code": code, "error": error, "params": params}

        if code:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_SUCCESS_PAGE.encode())
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_ERROR_PAGE.encode())

    def log_message(self, *args):
        pass   # suppress request logs


def _parse_callback_url(url: str) -> dict:
    """Extract code/error/params from a pasted callback URL.

    Accepts:
      - Full URL: http://localhost:55155/callback?code=xxx&state=yyy
      - Just the query string: ?code=xxx&state=yyy
      - Just the code value: 4/0AQSTgQH...
    """
    url = url.strip()
    if not url:
        return {"code": "", "error": "empty input", "params": {}}

    # If it looks like a bare code (no ? or & or /), treat it as the code
    if "?" not in url and "&" not in url and "/" not in url and len(url) > 10:
        return {"code": url, "error": "", "params": {"code": [url]}}

    # Parse as URL
    if url.startswith("?"):
        url = "http://localhost" + url

    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]
        return {"code": code, "error": error, "params": params}
    except Exception as e:
        return {"code": "", "error": f"could not parse URL: {e}", "params": {}}


def wait_for_callback(timeout: float = 120.0) -> dict:
    """Start the callback server and block until the code arrives.

    Returns {"code": "...", "error": "...", "params": {...}}.

    On headless systems, this is never called — use wait_for_callback_manual()
    instead. But if called on a headless system (e.g. port already in use),
    it falls back gracefully.
    """
    try:
        server = HTTPServer(("localhost", _PORT), _CallbackHandler)
    except OSError:
        # Port in use or binding failed — fall back to manual
        return {"code": "", "error": "could not bind localhost:55155 — use manual mode", "params": {}}

    server._result = {}

    def serve():
        server.handle_request()   # exactly one request then stop

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    t.join(timeout=timeout)
    server.server_close()
    return server._result


def wait_for_callback_manual(console=None) -> dict:
    """VPS/headless mode: ask the user to paste the callback URL.

    After the user completes OAuth in their local browser, the browser
    redirects to localhost:55155/callback?code=... which will fail to load
    (since the VPS isn't their local machine). The user copies that URL
    from their browser's address bar and pastes it here.

    Returns {"code": "...", "error": "...", "params": {...}}.
    """
    if console:
        from rich.text import Text
        from argus.theme import CYAN, DIM, FG, GOLD
        console.print()
        console.print(Text(
            "  ┌─────────────────────────────────────────────────────────┐",
            style=DIM,
        ))
        console.print(Text(
            "  │  VPS MODE — paste the callback URL from your browser    │",
            style=DIM,
        ))
        console.print(Text(
            "  └─────────────────────────────────────────────────────────┘",
            style=DIM,
        ))
        console.print(Text(
            "\n  After you authorize in your browser, it will redirect to a\n"
            "  localhost URL that won't load. That's expected on a VPS.\n"
            "  Copy the FULL URL from your browser's address bar and paste it below.\n",
            style=DIM,
        ))
        console.print(Text(
            "  It looks like: http://localhost:55155/callback?code=4/0AQSTg...\n",
            style=CYAN,
        ))

    # Use raw input since we might not have the picker available in all contexts
    try:
        from argus import picker
        raw = picker.text("Paste the full callback URL here:")
    except Exception:
        raw = input("  Paste the full callback URL here: ")

    if not raw:
        return {"code": "", "error": "no URL provided", "params": {}}

    return _parse_callback_url(raw)


def open_auth_url(url: str) -> None:
    """Open the auth URL in the user's default browser.

    On headless systems, prints the URL instead (the user copies it to
    their local browser manually).
    """
    if _is_headless():
        # Don't try to open a browser — just print clearly
        print(f"\n  Open this URL in your browser:\n\n  {url}\n")
        return

    import webbrowser
    try:
        webbrowser.open(url)
    except Exception:
        # Browser open failed — print the URL as fallback
        print(f"\n  Could not open browser. Open this URL manually:\n\n  {url}\n")

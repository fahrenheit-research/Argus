"""Local OAuth2 callback server.

Spins up a temporary HTTP server on localhost:55155 to catch the OAuth
redirect. Opens the auth URL in the default browser, then waits up to
120 seconds for the callback. Extracts the `code` parameter and shuts
down cleanly.

Compatible with Google, Microsoft, LinkedIn, Twitter OAuth flows — all
support localhost redirect URIs.
"""

from __future__ import annotations

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


def wait_for_callback(timeout: float = 120.0) -> dict:
    """Start the callback server and block until the code arrives.

    Returns {"code": "...", "error": "...", "params": {...}}.
    """
    server = HTTPServer(("localhost", _PORT), _CallbackHandler)
    server._result = {}

    def serve():
        server.handle_request()   # exactly one request then stop

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    t.join(timeout=timeout)
    server.server_close()
    return server._result


def open_auth_url(url: str) -> None:
    """Open the auth URL in the user's default browser."""
    import webbrowser
    webbrowser.open(url)

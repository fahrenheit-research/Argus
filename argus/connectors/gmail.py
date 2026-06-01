"""Gmail connector — Google OAuth2 with MCP server support.

Auth flow:
  1. User creates a Google Cloud OAuth 2.0 Client ID (Desktop app type)
     → https://console.cloud.google.com/apis/credentials
  2. User downloads credentials.json OR pastes client_id + client_secret
  3. ARGUS generates the auth URL and opens it in the browser
  4. Google redirects to localhost:55155/callback with the auth code
  5. ARGUS exchanges the code for access + refresh tokens (stored 0600)
  6. Gmail MCP server config written to ~/.argus/mcp_config.json

After setup the user can say:
  "read my latest emails"
  "search gmail for invoices"
  "send an email to alice@example.com"
"""

from __future__ import annotations

import json
import secrets
import urllib.parse

import httpx

from argus.connectors.base import BaseConnector, ConnectorStatus
from argus.connectors.oauth_server import REDIRECT_URI, open_auth_url, wait_for_callback
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA, ERR


_AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL  = "https://oauth2.googleapis.com/token"
_SCOPES     = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
]
_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class GmailConnector(BaseConnector):
    id          = "gmail"
    name        = "Gmail"
    icon        = "📧"
    description = "Read, search, and send emails via Gmail"
    auth_type   = "oauth2"
    triggers    = [
        "gmail", "google mail", "google email", "connect gmail",
        "link gmail", "setup gmail", "my emails", "connect google mail",
    ]
    setup_docs_url = "https://developers.google.com/gmail/api/quickstart/python"
    console_url    = "https://console.cloud.google.com/apis/credentials"
    mcp_command    = ["npx", "-y", "@modelcontextprotocol/server-gmail"]

    def status(self) -> ConnectorStatus:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return ConnectorStatus(connected=False)
        return ConnectorStatus(
            connected=True,
            account=tokens.get("email", ""),
            detail="OAuth2 tokens valid",
            tokens=tokens,
        )

    def setup_wizard(self, console) -> bool:
        from rich.text import Text
        from rich.padding import Padding
        from rich.panel import Panel
        from argus import picker

        console.print()

        # ── Step 1: Resolve credentials — three safe methods ────────────
        creds = self.load_credentials()
        if not creds:
            _guide(console)
            method = picker.select("How do you want to provide credentials?", choices=[
                picker.Choice("path",   "Enter the path to your credentials.json file"),
                picker.Choice("manual", "Enter client_id and client_secret manually"),
                picker.Choice("cancel", "Cancel — I'll set this up later"),
            ])
            if not method or method == "cancel":
                return False

            if method == "path":
                # Accepts EITHER a file path OR the JSON contents pasted directly.
                # Robust to: parenthetical hints in input ("foo.json) garbage"),
                # leading/trailing whitespace, surrounding quotes, JSON paste blobs.
                raw_input = picker.text(
                    "Path to credentials.json, OR paste the JSON contents directly:"
                )
                if not raw_input:
                    return False

                creds = _parse_credentials_input(raw_input, console)
                if not creds:
                    return False
            else:
                cid  = picker.text("Paste your Client ID:")
                csec = picker.password("Paste your Client Secret:")
                if not cid or not csec:
                    return False
                creds = {"client_id": cid.strip(), "client_secret": csec.strip(), "type": "oauth2"}

            if not creds.get("client_id"):
                console.print(Text("  ✗ No client_id found", style=ERR))
                return False
            self.save_credentials(creds)

        client_id     = creds["client_id"]
        client_secret = creds["client_secret"]

        # ── Step 2: Generate auth URL + open browser ────────────────────
        state = secrets.token_urlsafe(16)
        params = {
            "client_id":     client_id,
            "redirect_uri":  REDIRECT_URI,
            "response_type": "code",
            "scope":         " ".join(_SCOPES),
            "access_type":   "offline",
            "prompt":        "consent",
            "state":         state,
        }
        auth_url = _AUTH_URL + "?" + urllib.parse.urlencode(params)

        body = Text()
        body.append("\n  Opening Google auth in your browser…\n", style=FG)
        body.append("  If nothing opens, paste this URL:\n  ", style=DIM)
        body.append(auth_url, style=f"link {auth_url} {CYAN} underline")
        console.print(body)

        open_auth_url(auth_url)

        # ── Step 3: Wait for callback ───────────────────────────────────
        console.print(Text("\n  Waiting for Google to redirect (up to 120s)…", style=DIM))
        result = wait_for_callback(timeout=120.0)

        if result.get("error") or not result.get("code"):
            console.print(Text(f"  ✗ Auth failed: {result.get('error', 'no code received')}", style=ERR))
            return False

        code = result["code"]

        # ── Step 4: Exchange code for tokens ────────────────────────────
        try:
            async def exchange():
                async with httpx.AsyncClient(timeout=15) as c:
                    return await c.post(_TOKEN_URL, data={
                        "code":          code,
                        "client_id":     client_id,
                        "client_secret": client_secret,
                        "redirect_uri":  REDIRECT_URI,
                        "grant_type":    "authorization_code",
                    })
            import asyncio
            resp = asyncio.run(exchange())
            tokens = resp.json()
        except Exception as e:
            console.print(Text(f"  ✗ Token exchange failed: {e}", style=ERR))
            return False

        if "error" in tokens:
            console.print(Text(f"  ✗ {tokens['error']}: {tokens.get('error_description', '')}", style=ERR))
            return False

        # ── Step 5: Fetch email address ─────────────────────────────────
        try:
            async def get_email():
                async with httpx.AsyncClient(timeout=10) as c:
                    return await c.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {tokens['access_token']}"},
                    )
            resp2 = asyncio.run(get_email())
            info = resp2.json()
            tokens["email"] = info.get("email", "")
        except Exception:
            pass

        self.save_tokens(tokens)

        # ── Step 6: Write MCP config ─────────────────────────────────────
        mcp_cfg = self.mcp_config()
        if mcp_cfg:
            from argus.tools.mcp_setup import handle_mcp_config
            handle_mcp_config(json.dumps({"mcpServers": mcp_cfg}), console)

        console.print()
        console.print(Text(
            f"  ✓ Gmail connected as {tokens.get('email', 'unknown')}\n"
            f"  Tokens stored at {self._token_file}\n"
            f"  You can now say: 'read my latest emails'",
            style=f"bold {GOLD}",
        ))
        return True

    def mcp_config(self) -> dict | None:
        tokens = self.load_tokens()
        creds  = self.load_credentials()
        if not tokens.get("access_token") or not creds.get("client_id"):
            return None
        return {
            "gmail": {
                "command": "npx",
                "args":    ["-y", "@modelcontextprotocol/server-gmail"],
                "env": {
                    "GMAIL_CLIENT_ID":     creds["client_id"],
                    "GMAIL_CLIENT_SECRET": creds["client_secret"],
                    "GMAIL_REFRESH_TOKEN": tokens.get("refresh_token", ""),
                },
            }
        }

    async def test_connection(self) -> tuple[bool, str]:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return False, "not connected"
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )
            if r.status_code == 200:
                data = r.json()
                return True, f"connected as {data.get('emailAddress', '?')}"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)[:60]


def _guide(console) -> None:
    from rich.text import Text
    body = Text()
    body.append("\n  Gmail Setup — get your credentials in 2 minutes:\n\n", style=f"bold {GOLD}")
    steps = [
        ("1", "Open Google Cloud Console → APIs & Services → Credentials",
         "https://console.cloud.google.com/apis/credentials"),
        ("2", "Create credentials → OAuth 2.0 Client ID → Desktop app", None),
        ("3", "Enable the Gmail API at:",
         "https://console.cloud.google.com/apis/library/gmail.googleapis.com"),
        ("4", "Download credentials.json and paste it below", None),
    ]
    for num, text, url in steps:
        body.append(f"  {num}. ", style=f"bold {MAGENTA}")
        body.append(text, style=FG)
        if url:
            body.append("\n     ")
            body.append(url, style=f"link {url} {CYAN} underline")
        body.append("\n")
    console.print(body)


def _parse_credentials_input(raw_input: str, console) -> dict | None:
    """Accept either a file path OR pasted JSON contents.

    Bulletproof against common paste-pollution patterns:
      • Path with parenthetical hint: "(/Users/me/credentials.json):"
      • JSON content pasted after the path
      • Leading/trailing whitespace, quotes, newlines
      • Mixed: path on first line, JSON below it
    """
    from rich.text import Text
    import os
    import re
    from pathlib import Path

    raw_input = raw_input.strip()
    if not raw_input:
        return None

    # ── Strategy 1: pasted JSON anywhere in the input ────────────────────
    # If the input contains a `{`, try extracting and parsing JSON directly.
    if "{" in raw_input and "}" in raw_input:
        # Find the outermost JSON object
        first_brace = raw_input.find("{")
        last_brace  = raw_input.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            json_candidate = raw_input[first_brace : last_brace + 1]
            try:
                data = json.loads(json_candidate)
                installed = data.get("installed") or data.get("web") or data
                cid  = installed.get("client_id", "")
                csec = installed.get("client_secret", "")
                if cid and csec:
                    console.print(Text("  ✓ Parsed credentials JSON from paste", style=GOLD))
                    return {"client_id": cid, "client_secret": csec, "type": "oauth2"}
            except json.JSONDecodeError:
                pass   # fall through to path-based parsing

    # ── Strategy 2: extract a file path from polluted input ─────────────
    # Pull out the first thing that looks like an absolute path.
    # Path = leading "/" or "~/" through to whitespace/quote/paren/colon.
    path_match = re.search(r"(?:~/|/)[^\s'\"():,]+\.json", raw_input)
    if path_match:
        candidate = path_match.group(0)
        fpath = Path(os.path.expanduser(candidate)).resolve()
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text())
                installed = data.get("installed") or data.get("web") or data
                cid  = installed.get("client_id", "")
                csec = installed.get("client_secret", "")
                if cid and csec:
                    console.print(Text(f"  ✓ Loaded credentials from {fpath.name}", style=GOLD))
                    return {"client_id": cid, "client_secret": csec, "type": "oauth2"}
                console.print(Text(
                    f"  ✗ File parsed but client_id or client_secret missing in {fpath.name}",
                    style=ERR,
                ))
                return None
            except json.JSONDecodeError as e:
                console.print(Text(f"  ✗ Could not parse {fpath.name}: {e}", style=ERR))
                return None

    # ── Strategy 3: maybe the whole input is a path with hint text ──────
    # Try stripping common pollution patterns
    cleaned = re.sub(r"[(),:]+\s*$", "", raw_input.split("\n")[0]).strip().strip("'\"")
    if cleaned:
        fpath = Path(os.path.expanduser(cleaned)).resolve()
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text())
                installed = data.get("installed") or data.get("web") or data
                cid  = installed.get("client_id", "")
                csec = installed.get("client_secret", "")
                if cid and csec:
                    console.print(Text(f"  ✓ Loaded credentials from {fpath.name}", style=GOLD))
                    return {"client_id": cid, "client_secret": csec, "type": "oauth2"}
            except json.JSONDecodeError:
                pass

    # Nothing worked — clear error
    console.print(Text(
        "  ✗ Couldn't read credentials.json.\n"
        "    Either paste the full path to the file (e.g. ~/Downloads/credentials.json)\n"
        "    OR paste the JSON contents directly (starts with `{`).\n",
        style=ERR,
    ))
    return None

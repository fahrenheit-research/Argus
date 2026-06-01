"""Google Docs connector — read, create, and edit Google Docs via Drive API."""

from __future__ import annotations

import json
import secrets
import urllib.parse

import httpx

from argus.connectors.base import BaseConnector, ConnectorStatus
from argus.connectors.oauth_server import REDIRECT_URI, open_auth_url, wait_for_callback
from argus.theme import CYAN, DIM, FG, GOLD, ERR

_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPES    = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GoogleDocsConnector(BaseConnector):
    id          = "google_docs"
    name        = "Google Docs"
    icon        = "📝"
    description = "Read, create and edit Google Docs and Drive files"
    auth_type   = "oauth2"
    triggers    = [
        "google docs", "google doc", "connect docs", "link docs",
        "setup google docs", "google drive", "connect drive",
    ]
    setup_docs_url = "https://console.cloud.google.com/apis/library/docs.googleapis.com"
    console_url    = "https://console.cloud.google.com/apis/credentials"

    def status(self) -> ConnectorStatus:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return ConnectorStatus(connected=False)
        return ConnectorStatus(
            connected=True,
            account=tokens.get("email", ""),
            detail="Google Docs OAuth2 valid",
            tokens=tokens,
        )

    def setup_wizard(self, console) -> bool:
        from rich.text import Text
        from argus import picker

        console.print()

        # Try to reuse Gmail credentials
        from argus.connectors.gmail import GmailConnector
        gmail_creds = GmailConnector().load_credentials()
        if gmail_creds.get("client_id"):
            reuse = picker.confirm("Reuse your Gmail OAuth credentials for Google Docs?", default=True)
            if reuse:
                self.save_credentials(gmail_creds)

        creds = self.load_credentials()
        if not creds.get("client_id"):
            body = Text()
            body.append("\n  📝 Google Docs Setup\n\n", style=f"bold {GOLD}")
            body.append("  Enable Google Docs API at:\n  ", style=DIM)
            body.append(self.setup_docs_url, style=f"link {self.setup_docs_url} {CYAN} underline")
            body.append("\n  Get credentials at:\n  ", style=DIM)
            body.append(self.console_url, style=f"link {self.console_url} {CYAN} underline")
            console.print(body)

            raw_input = picker.text("Path to credentials.json, OR paste the JSON contents directly:")
            if not raw_input:
                return False
            from argus.connectors.gmail import _parse_credentials_input
            creds = _parse_credentials_input(raw_input, console)
            if not creds:
                return False
            self.save_credentials(creds)

        state = secrets.token_urlsafe(16)
        params = {
            "client_id":     creds["client_id"],
            "redirect_uri":  REDIRECT_URI,
            "response_type": "code",
            "scope":         " ".join(_SCOPES),
            "access_type":   "offline",
            "prompt":        "consent",
            "state":         state,
        }
        auth_url = _AUTH_URL + "?" + urllib.parse.urlencode(params)
        console.print(Text("\n  Opening Google Docs auth in your browser…", style=FG))
        open_auth_url(auth_url)

        from argus.connectors.oauth_server import _is_headless, wait_for_callback_manual
        if _is_headless():
            console.print(Text("\n  VPS/headless mode detected — paste the callback URL below.", style=DIM))
            result = wait_for_callback_manual(console)
        else:
            console.print(Text("\n  Waiting for redirect (up to 120s)…", style=DIM))
            result = wait_for_callback(timeout=120.0)

        if not result.get("code"):
            console.print(Text(f"  ✗ {result.get('error', 'no code')}", style=ERR))
            return False

        import asyncio

        async def exchange():
            async with httpx.AsyncClient(timeout=15) as c:
                return await c.post(_TOKEN_URL, data={
                    "code": result["code"], "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                    "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
                })

        tokens = asyncio.run(exchange()).json()
        if "error" in tokens:
            console.print(Text(f"  ✗ {tokens['error']}", style=ERR))
            return False

        async def get_email():
            async with httpx.AsyncClient(timeout=8) as c:
                return await c.get("https://www.googleapis.com/oauth2/v2/userinfo",
                                   headers={"Authorization": f"Bearer {tokens['access_token']}"})
        try:
            tokens["email"] = asyncio.run(get_email()).json().get("email", "")
        except Exception:
            pass

        self.save_tokens(tokens)
        console.print(Text(
            f"\n  ✓ Google Docs connected as {tokens.get('email', '?')}\n"
            f"  Say: 'create a doc called Meeting Notes' or 'read my recent docs'",
            style=f"bold {GOLD}",
        ))
        return True

    async def test_connection(self) -> tuple[bool, str]:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return False, "not connected"
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    "https://docs.googleapis.com/v1/documents?pageSize=1",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )
            return (True, f"connected as {tokens.get('email', '?')}") if r.status_code in (200, 204) \
                else (False, f"HTTP {r.status_code}")
        except Exception as e:
            return False, str(e)[:60]

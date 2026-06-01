"""LinkedIn connector — OAuth 2.0 with user-provided app credentials."""

from __future__ import annotations

import json
import secrets
import urllib.parse

import httpx

from argus.connectors.base import BaseConnector, ConnectorStatus
from argus.connectors.oauth_server import REDIRECT_URI, open_auth_url, wait_for_callback
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA, ERR

_AUTH_URL  = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_SCOPES    = ["openid", "profile", "email", "w_member_social"]


class LinkedInConnector(BaseConnector):
    id          = "linkedin"
    name        = "LinkedIn"
    icon        = "💼"
    description = "Read your profile and post updates on LinkedIn"
    auth_type   = "oauth2"
    triggers    = [
        "linkedin", "connect linkedin", "link linkedin", "setup linkedin",
        "my linkedin", "linkedin profile",
    ]
    setup_docs_url = "https://www.linkedin.com/developers/apps"
    console_url    = "https://www.linkedin.com/developers/apps/new"

    def status(self) -> ConnectorStatus:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return ConnectorStatus(connected=False)
        return ConnectorStatus(
            connected=True,
            account=tokens.get("name", ""),
            detail="LinkedIn OAuth2 token valid",
            tokens=tokens,
        )

    def setup_wizard(self, console) -> bool:
        from rich.text import Text
        from argus import picker

        console.print()
        body = Text()
        body.append(f"\n  {self.icon} LinkedIn Setup\n\n", style=f"bold {GOLD}")
        body.append("  Create a LinkedIn app at:\n  ", style=DIM)
        body.append(self.console_url, style=f"link {self.console_url} {CYAN} underline")
        body.append("\n\n", style=DIM)
        body.append("  Required settings:\n", style=DIM)
        body.append("    • Add redirect URL: ", style=DIM)
        body.append(REDIRECT_URI, style=CYAN)
        body.append("\n    • Request products: Sign In with LinkedIn + Share on LinkedIn\n", style=DIM)
        console.print(body)

        creds = self.load_credentials()
        if not creds.get("client_id"):
            cid  = picker.text("Paste your LinkedIn App Client ID:")
            csec = picker.password("Paste your LinkedIn App Client Secret:")
            if not cid or not csec:
                return False
            creds = {"client_id": cid.strip(), "client_secret": csec.strip(), "type": "oauth2"}
            self.save_credentials(creds)

        state = secrets.token_urlsafe(16)
        params = {
            "response_type": "code",
            "client_id":     creds["client_id"],
            "redirect_uri":  REDIRECT_URI,
            "scope":         " ".join(_SCOPES),
            "state":         state,
        }
        auth_url = _AUTH_URL + "?" + urllib.parse.urlencode(params)

        console.print(Text(f"\n  Opening LinkedIn login in your browser…", style=FG))
        open_auth_url(auth_url)
        console.print(Text("\n  Waiting for LinkedIn redirect (up to 120s)…", style=DIM))
        result = wait_for_callback(timeout=120.0)

        if not result.get("code"):
            console.print(Text(f"  ✗ {result.get('error', 'no code received')}", style=ERR))
            return False

        import asyncio

        async def exchange():
            async with httpx.AsyncClient(timeout=15) as c:
                return await c.post(_TOKEN_URL, data={
                    "grant_type":    "authorization_code",
                    "code":          result["code"],
                    "redirect_uri":  REDIRECT_URI,
                    "client_id":     creds["client_id"],
                    "client_secret": creds["client_secret"],
                })

        resp = asyncio.run(exchange())
        tokens = resp.json()

        if "error" in tokens or not tokens.get("access_token"):
            console.print(Text(f"  ✗ {tokens}", style=ERR))
            return False

        async def get_profile():
            async with httpx.AsyncClient(timeout=8) as c:
                return await c.get(
                    "https://api.linkedin.com/v2/userinfo",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )

        try:
            pr = asyncio.run(get_profile())
            info = pr.json()
            tokens["name"]  = info.get("name", "")
            tokens["email"] = info.get("email", "")
        except Exception:
            pass

        self.save_tokens(tokens)
        console.print(Text(
            f"\n  ✓ LinkedIn connected as {tokens.get('name', '?')} ({tokens.get('email', '')})\n"
            f"  Say: 'post on linkedin' or 'read my linkedin profile'",
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
                    "https://api.linkedin.com/v2/userinfo",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )
            if r.status_code == 200:
                return True, f"connected as {tokens.get('name', '?')}"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)[:60]

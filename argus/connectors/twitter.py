"""Twitter / X connector — OAuth 2.0 with PKCE (no client secret required).

Uses Twitter's OAuth 2.0 PKCE flow — the user only needs a Twitter Developer
App Client ID (public, free tier). No client secret is needed.

App registration: https://developer.twitter.com/en/portal/dashboard
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import urllib.parse

import httpx

from argus.connectors.base import BaseConnector, ConnectorStatus
from argus.connectors.oauth_server import REDIRECT_URI, open_auth_url, wait_for_callback
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA, ERR

_AUTH_URL  = "https://twitter.com/i/oauth2/authorize"
_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
_SCOPES    = ["tweet.read", "tweet.write", "users.read", "offline.access"]


def _pkce_pair() -> tuple[str, str]:
    """Generate code_verifier + code_challenge (S256)."""
    verifier  = secrets.token_urlsafe(43)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class TwitterConnector(BaseConnector):
    id          = "twitter"
    name        = "Twitter / X"
    icon        = "𝕏"
    description = "Read your timeline and post tweets on Twitter / X"
    auth_type   = "oauth2"
    triggers    = [
        "twitter", "x (twitter)", "tweet", "connect twitter", "link twitter",
        "setup twitter", "my twitter", "connect x", "link x", "twitter account",
    ]
    setup_docs_url = "https://developer.twitter.com/en/portal/dashboard"
    console_url    = "https://developer.twitter.com/en/portal/projects-and-apps"

    def status(self) -> ConnectorStatus:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return ConnectorStatus(connected=False)
        return ConnectorStatus(
            connected=True,
            account=tokens.get("username", ""),
            detail="Twitter OAuth2 PKCE token valid",
            tokens=tokens,
        )

    def setup_wizard(self, console) -> bool:
        from rich.text import Text
        from argus import picker

        console.print()
        body = Text()
        body.append(f"\n  𝕏 Twitter / X Setup\n\n", style=f"bold {GOLD}")
        body.append("  Get a free Twitter Developer App Client ID at:\n  ", style=DIM)
        body.append(self.console_url, style=f"link {self.console_url} {CYAN} underline")
        body.append("\n\n  Required app settings:\n", style=DIM)
        body.append("    • Type: Native App\n", style=DIM)
        body.append("    • OAuth 2.0: ON\n", style=DIM)
        body.append("    • Callback URL: ", style=DIM)
        body.append(REDIRECT_URI, style=CYAN)
        body.append("\n    • App permissions: Read and Write\n", style=DIM)
        console.print(body)

        creds = self.load_credentials()
        if not creds.get("client_id"):
            cid = picker.text("Paste your Twitter App Client ID:")
            if not cid:
                return False
            creds = {"client_id": cid.strip(), "type": "oauth2_pkce"}
            self.save_credentials(creds)

        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        params = {
            "response_type":         "code",
            "client_id":             creds["client_id"],
            "redirect_uri":          REDIRECT_URI,
            "scope":                 " ".join(_SCOPES),
            "state":                 state,
            "code_challenge":        challenge,
            "code_challenge_method": "S256",
        }
        auth_url = _AUTH_URL + "?" + urllib.parse.urlencode(params)

        console.print(Text(f"\n  Opening Twitter login in your browser…", style=FG))
        open_auth_url(auth_url)
        console.print(Text("\n  Waiting for Twitter redirect (up to 120s)…", style=DIM))
        result = wait_for_callback(timeout=120.0)

        if not result.get("code"):
            console.print(Text(f"  ✗ {result.get('error', 'no code received')}", style=ERR))
            return False

        import asyncio

        async def exchange():
            async with httpx.AsyncClient(timeout=15) as c:
                return await c.post(
                    _TOKEN_URL,
                    data={
                        "code":          result["code"],
                        "grant_type":    "authorization_code",
                        "client_id":     creds["client_id"],
                        "redirect_uri":  REDIRECT_URI,
                        "code_verifier": verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

        resp = asyncio.run(exchange())
        tokens = resp.json()

        if "error" in tokens or not tokens.get("access_token"):
            console.print(Text(f"  ✗ {tokens}", style=ERR))
            return False

        async def get_me():
            async with httpx.AsyncClient(timeout=8) as c:
                return await c.get(
                    "https://api.twitter.com/2/users/me",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )

        try:
            me = asyncio.run(get_me())
            info = me.json().get("data", {})
            tokens["username"] = info.get("username", "")
            tokens["name"]     = info.get("name", "")
        except Exception:
            pass

        self.save_tokens(tokens)
        console.print(Text(
            f"\n  ✓ Twitter / X connected as @{tokens.get('username', '?')}\n"
            f"  Say: 'post a tweet' or 'read my twitter timeline'",
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
                    "https://api.twitter.com/2/users/me",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )
            if r.status_code == 200:
                return True, f"@{tokens.get('username', '?')}"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)[:60]

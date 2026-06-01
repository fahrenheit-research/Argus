"""Outlook / Microsoft 365 connector — Microsoft OAuth2 (public client, no secret)."""

from __future__ import annotations

import secrets
import urllib.parse

import httpx

from argus.connectors.base import BaseConnector, ConnectorStatus
from argus.connectors.oauth_server import REDIRECT_URI, open_auth_url, wait_for_callback
from argus.theme import CYAN, DIM, FG, GOLD, MAGENTA, ERR

_AUTH_URL  = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_SCOPES    = [
    "https://graph.microsoft.com/Mail.Read",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Calendars.ReadWrite",
    "https://graph.microsoft.com/User.Read",
    "offline_access",
]
# Microsoft provides a public multi-tenant app for personal use.
# Users can also register their own at https://portal.azure.com
_DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"   # Microsoft's public client for demos


class OutlookConnector(BaseConnector):
    id          = "outlook"
    name        = "Outlook / Microsoft 365"
    icon        = "📬"
    description = "Read, search and send emails via Outlook / Microsoft 365"
    auth_type   = "oauth2"
    triggers    = [
        "outlook", "microsoft mail", "office 365", "hotmail", "office365",
        "connect outlook", "link outlook", "setup outlook", "microsoft email",
    ]
    setup_docs_url = "https://learn.microsoft.com/en-us/graph/auth-v2-user"
    console_url    = "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
    mcp_command    = []     # no official MCP yet — use Graph API directly

    def status(self) -> ConnectorStatus:
        tokens = self.load_tokens()
        if not tokens.get("access_token"):
            return ConnectorStatus(connected=False)
        return ConnectorStatus(
            connected=True,
            account=tokens.get("email", ""),
            detail="Microsoft OAuth2 token valid",
            tokens=tokens,
        )

    def setup_wizard(self, console) -> bool:
        from rich.text import Text
        from argus import picker

        console.print()
        body = Text()
        body.append(f"\n  {self.icon} Outlook Setup\n\n", style=f"bold {GOLD}")
        body.append("  Using Microsoft public OAuth client (no credentials needed).\n", style=DIM)
        body.append("  For enterprise / own Azure app → register at:\n  ", style=DIM)
        body.append(self.console_url, style=f"link {self.console_url} {CYAN} underline")
        body.append("\n")
        console.print(body)

        creds = self.load_credentials()
        client_id = creds.get("client_id") or _DEFAULT_CLIENT_ID

        ask_custom = picker.confirm("Use a custom Azure App client_id?", default=False)
        if ask_custom:
            cid = picker.text("Paste your Azure App client_id:")
            if cid:
                client_id = cid.strip()
                self.save_credentials({"client_id": client_id, "type": "oauth2"})

        state = secrets.token_urlsafe(16)
        params = {
            "client_id":     client_id,
            "response_type": "code",
            "redirect_uri":  REDIRECT_URI,
            "scope":         " ".join(_SCOPES),
            "state":         state,
            "response_mode": "query",
        }
        auth_url = _AUTH_URL + "?" + urllib.parse.urlencode(params)

        body2 = Text()
        body2.append("\n  Opening Microsoft login in your browser…\n  ", style=FG)
        body2.append(auth_url[:80] + "…", style=f"link {auth_url} {CYAN} underline")
        console.print(body2)

        open_auth_url(auth_url)
        console.print(Text("\n  Waiting for Microsoft redirect (up to 120s)…", style=DIM))
        result = wait_for_callback(timeout=120.0)

        if not result.get("code"):
            console.print(Text(f"  ✗ {result.get('error', 'no code received')}", style=ERR))
            return False

        import asyncio

        async def exchange():
            async with httpx.AsyncClient(timeout=15) as c:
                return await c.post(_TOKEN_URL, data={
                    "client_id":    client_id,
                    "code":         result["code"],
                    "redirect_uri": REDIRECT_URI,
                    "grant_type":   "authorization_code",
                    "scope":        " ".join(_SCOPES),
                })

        resp = asyncio.run(exchange())
        tokens = resp.json()
        if "error" in tokens:
            console.print(Text(f"  ✗ {tokens['error']}: {tokens.get('error_description', '')}", style=ERR))
            return False

        async def get_profile():
            async with httpx.AsyncClient(timeout=8) as c:
                return await c.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )

        try:
            pr = asyncio.run(get_profile())
            info = pr.json()
            tokens["email"] = info.get("mail") or info.get("userPrincipalName", "")
        except Exception:
            pass

        self.save_tokens(tokens)
        console.print(Text(
            f"\n  ✓ Outlook connected as {tokens.get('email', '?')}\n"
            f"  Say: 'read my latest emails' or 'search outlook for invoices'",
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
                    "https://graph.microsoft.com/v1.0/me/messages?$top=1&$select=subject",
                    headers={"Authorization": f"Bearer {tokens['access_token']}"},
                )
            if r.status_code == 200:
                return True, f"connected as {tokens.get('email', '?')}"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)[:60]

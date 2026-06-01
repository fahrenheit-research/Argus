"""Base connector class — all service connectors inherit from this."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus import paths


CONNECTORS_DIR = paths.HOME / "connectors"


@dataclass
class ConnectorStatus:
    connected: bool
    account:   str = ""        # e.g. "user@gmail.com"
    detail:    str = ""
    tokens:    dict = field(default_factory=dict)


class BaseConnector(ABC):
    # ── Required attributes ────────────────────────────────────────────
    id:          str   # machine name: "gmail", "outlook", …
    name:        str   # display name: "Gmail"
    icon:        str   # emoji: "📧"
    description: str   # one-liner shown in connector list
    auth_type:   str   # "oauth2" | "api_key" | "none"

    # NL triggers — any of these in the user's message triggers this connector
    triggers: list[str] = []

    # URLs shown to the user before they create credentials
    setup_docs_url:   str = ""
    console_url:      str = ""

    # OAuth config — subclasses fill these in
    oauth_auth_url:  str = ""
    oauth_token_url: str = ""
    oauth_scopes:    list[str] = []

    # MCP server command (if an official MCP server exists)
    mcp_command: list[str] = []

    # ── Storage helpers ────────────────────────────────────────────────

    @property
    def _store_dir(self) -> Path:
        d = CONNECTORS_DIR / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def _token_file(self) -> Path:
        return self._store_dir / "tokens.json"

    @property
    def _creds_file(self) -> Path:
        return self._store_dir / "credentials.json"

    def save_tokens(self, tokens: dict[str, Any]) -> None:
        self._token_file.write_text(json.dumps(tokens, indent=2))
        self._token_file.chmod(0o600)

    def load_tokens(self) -> dict[str, Any]:
        if self._token_file.exists():
            try:
                return json.loads(self._token_file.read_text())
            except Exception:
                pass
        return {}

    def save_credentials(self, creds: dict[str, Any]) -> None:
        self._creds_file.write_text(json.dumps(creds, indent=2))
        self._creds_file.chmod(0o600)

    def load_credentials(self) -> dict[str, Any]:
        if self._creds_file.exists():
            try:
                return json.loads(self._creds_file.read_text())
            except Exception:
                pass
        return {}

    def is_connected(self) -> bool:
        tokens = self.load_tokens()
        return bool(tokens.get("access_token") or tokens.get("api_key"))

    def mcp_config(self) -> dict[str, Any] | None:
        """Return mcpServers entry for ~/.argus/mcp_config.json, or None."""
        return None

    # ── Subclass interface ────────────────────────────────────────────

    @abstractmethod
    def status(self) -> ConnectorStatus:
        """Return current connection status (fast, no network)."""

    @abstractmethod
    def setup_wizard(self, console) -> bool:
        """Interactive setup wizard. Returns True on success."""

    async def test_connection(self) -> tuple[bool, str]:
        """Make a cheap API call to verify credentials. Returns (ok, detail)."""
        return True, "not implemented"

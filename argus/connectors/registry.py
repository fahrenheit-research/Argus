"""Connector registry — discover, list, detect intent, and run wizards.

All connectors are registered here. Natural-language detection
("connect gmail", "link my twitter", etc.) maps to the right connector
so the user never has to remember a command syntax.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argus.connectors.base import BaseConnector

# ── Registry ──────────────────────────────────────────────────────────────────

def _build_registry() -> dict[str, "BaseConnector"]:
    from argus.connectors.gmail            import GmailConnector
    from argus.connectors.outlook          import OutlookConnector
    from argus.connectors.linkedin         import LinkedInConnector
    from argus.connectors.twitter          import TwitterConnector
    from argus.connectors.google_calendar  import GoogleCalendarConnector
    from argus.connectors.google_docs      import GoogleDocsConnector
    connectors = [
        GmailConnector(), OutlookConnector(),
        LinkedInConnector(), TwitterConnector(),
        GoogleCalendarConnector(), GoogleDocsConnector(),
    ]
    return {c.id: c for c in connectors}


# Lazy-initialise once
_REGISTRY: dict[str, "BaseConnector"] | None = None


def _registry() -> dict[str, "BaseConnector"]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


CONNECTORS = property(lambda self: _registry())


def get_connector(connector_id: str) -> "BaseConnector | None":
    return _registry().get(connector_id)


def list_connectors() -> list["BaseConnector"]:
    return list(_registry().values())


# ── Natural-language detection ────────────────────────────────────────────────

# Verb set that flags a "connect this service" intent.
_CONNECT_VERBS = frozenset({
    "connect", "link", "setup", "set up", "add", "attach",
    "configure", "enable", "integrate", "use", "start",
})

def detect_connect_intent(text: str) -> "BaseConnector | None":
    """Return the matching connector if the text looks like a 'connect X' request."""
    t = text.strip().lower()
    for connector in _registry().values():
        for trigger in connector.triggers:
            # Exact or fuzzy: contains trigger OR matches "verb trigger"
            if trigger in t:
                # Exclude generic nouns without a connect verb
                # (e.g. "gmail" alone might just be a question about gmail)
                has_verb = any(v in t for v in _CONNECT_VERBS)
                is_exact = t in connector.triggers
                if has_verb or is_exact or t.startswith("connect") or t.startswith("link"):
                    return connector
    return None


# ── Google service-account / OAuth JSON detection ────────────────────────────

_GOOGLE_AUTH_PATTERNS = (
    '"type": "service_account"',
    '"type":"service_account"',
    '"installed": {',
    '"installed":{',
    '"web": {',
    '"client_id":',
    '"client_secret":',
    '"private_key_id":',
)

_MICROSOFT_AUTH_PATTERNS = (
    '"tenantId"',
    '"clientId"',
    '"clientSecret"',
    'microsoftonline',
    'graph.microsoft.com',
)


def detect_auth_json(text: str) -> str | None:
    """If text looks like a Google or Microsoft auth JSON, return a category."""
    if not text.strip().startswith("{"):
        return None
    lower = text.lower()
    if any(p.lower() in lower for p in _GOOGLE_AUTH_PATTERNS):
        return "google_auth"
    if any(p.lower() in lower for p in _MICROSOFT_AUTH_PATTERNS):
        return "microsoft_auth"
    return None


# ── Wizard runner ─────────────────────────────────────────────────────────────


def run_connect_wizard(connector_id: str, console) -> bool:
    """Run the full setup wizard for the given connector."""
    c = get_connector(connector_id)
    if c is None:
        from rich.text import Text
        from argus.theme import ERR
        console.print(Text(f"  ✗ Unknown connector: {connector_id}", style=ERR))
        return False
    return c.setup_wizard(console)

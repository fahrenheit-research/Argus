"""Faux Telegram surface — getMe, token validation, fake message tail."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


@dataclass(frozen=True)
class BotIdentity:
    id: int
    username: str
    first_name: str


def validate_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token.strip()))


def get_me(token: str) -> BotIdentity | None:
    """Faux Telegram getMe — returns a BotIdentity if the shape is valid."""
    time.sleep(0.4)  # simulate one round-trip
    if not validate_token(token):
        return None
    return BotIdentity(id=78_249_193, username="my_argus_bot", first_name="My ARGUS Agent")


# A few canned incoming events for `gateway start` to render.
TAIL_EVENTS: list[tuple[float, str, str]] = [
    (0.0, "info", "long-poll cycle started"),
    (1.2, "info", "← @aniket: \"summarise PRs opened today against the argus repo\""),
    (1.4, "tool", "calling github.list_prs(repo='argus-org/argus', since='24h')…"),
    (3.6, "tool", "done in 2.1s — 4 PRs found"),
    (4.0, "info", "→ stream started (provider=groq, model=llama-3.3-70b)"),
    (6.4, "info", "→ stream ended (1,184 tokens out, 1.3s ttft)"),
    (6.5, "info", "reaction set: ✓"),
]

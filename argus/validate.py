"""Live key & token validation used by the setup wizard and `argus doctor`.

Wraps the provider `validate_key()` calls and Telegram's `getMe` in
synchronous functions with sensible timeouts so the UI can show
inline ✓ / ✗ status without an async context manager."""

from __future__ import annotations

import asyncio
import re

import httpx

from argus.config import Config
from argus.providers import ProviderError, transport_for


_TELEGRAM_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def validate_provider_key(provider_id: str, api_key: str, *, timeout: float = 12.0) -> tuple[bool, str]:
    """Synchronous wrapper around transport.validate_key(). Used by the
    setup wizard right after the user pastes a key, so we can fail fast
    with a clear error before they finish onboarding."""

    cfg = Config()
    cfg.env = {**cfg.env}  # mutate a copy
    # Stash the key on the throwaway config so transport_for picks it up.
    from argus.data.providers import get_provider as get_pinfo
    info = get_pinfo(provider_id)
    cfg.env[info.env_var] = api_key

    async def run() -> tuple[bool, str]:
        try:
            async with transport_for(provider_id, cfg) as t:
                return await asyncio.wait_for(t.validate_key(), timeout=timeout)
        except ProviderError as e:
            return False, str(e)
        except asyncio.TimeoutError:
            return False, f"{provider_id}: validation timed out after {timeout:.0f}s"
        except Exception as e:  # noqa: BLE001 — surface everything
            return False, f"{provider_id}: {type(e).__name__}: {e}"

    return asyncio.run(run())


def validate_telegram_token(token: str, *, timeout: float = 8.0) -> tuple[bool, str]:
    """Hit Telegram's `getMe` and confirm the token shape + a 200 response."""
    if not _TELEGRAM_TOKEN_RE.match(token.strip()):
        return False, "token shape is wrong — copy the whole `<id>:<secret>` line from BotFather"
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token.strip()}/getMe", timeout=timeout)
    except httpx.HTTPError as e:
        return False, f"network error: {e}"
    if r.status_code == 401:
        return False, "Telegram returned 401 — token revoked or wrong; re-copy from BotFather"
    if r.status_code != 200:
        return False, f"Telegram returned {r.status_code}: {r.text[:200]}"
    try:
        data = r.json()
    except ValueError:
        return False, "Telegram returned non-JSON"
    if not data.get("ok"):
        return False, f"Telegram returned not-ok: {data.get('description', '')}"
    result = data.get("result", {})
    return True, f"@{result.get('username', '?')} ({result.get('first_name', '?')})"

"""Anthropic prompt caching — `system_and_3` strategy.

Port of `hermes-agent/agent/prompt_caching.py`. Four cache_control
breakpoints: the system prompt plus the last 3 non-system messages,
all at the same TTL (`5m` by default, optional `1h`).

Anthropic charges:
- 1.25× the base input rate to WRITE a cache block (5m TTL)
- 0.10× to READ a cache hit

So break-even is ~2 reuses within the TTL. Multi-turn conversations
within one session easily clear that.
"""

from __future__ import annotations

import copy
from typing import Any


def _build_marker(ttl: str) -> dict[str, str]:
    marker: dict[str, str] = {"type": "ephemeral"}
    if ttl == "1h":
        marker["ttl"] = "1h"
    return marker


def _apply_cache_marker(msg: dict, marker: dict, *, native_anthropic: bool = False) -> None:
    """Attach `cache_control` to the last text block of one message,
    handling all of: empty content, string content, list-of-blocks content."""
    role = msg.get("role", "")
    content = msg.get("content")

    if role == "tool":
        if native_anthropic:
            msg["cache_control"] = marker
        return

    if content is None or content == "":
        msg["cache_control"] = marker
        return

    if isinstance(content, str):
        msg["content"] = [
            {"type": "text", "text": content, "cache_control": marker}
        ]
        return

    if isinstance(content, list) and content:
        last = content[-1]
        if isinstance(last, dict):
            last["cache_control"] = marker


def apply_anthropic_cache_control(
    api_messages: list[dict[str, Any]],
    cache_ttl: str = "5m",
    *,
    native_anthropic: bool = False,
) -> list[dict[str, Any]]:
    """Inject up to 4 `cache_control` breakpoints (system + last 3 non-system).

    Returns a deep copy so the caller's message list is untouched (matters
    for the Hermes pattern where the conversation buffer is shared across
    retries — we never want a retry to compound cache markers).
    """
    messages = copy.deepcopy(api_messages)
    if not messages:
        return messages

    marker = _build_marker(cache_ttl)
    breakpoints_used = 0

    if messages[0].get("role") == "system":
        _apply_cache_marker(messages[0], marker, native_anthropic=native_anthropic)
        breakpoints_used += 1

    remaining = 4 - breakpoints_used
    non_sys = [i for i in range(len(messages)) if messages[i].get("role") != "system"]
    for idx in non_sys[-remaining:]:
        _apply_cache_marker(messages[idx], marker, native_anthropic=native_anthropic)

    return messages


__all__ = ["apply_anthropic_cache_control"]

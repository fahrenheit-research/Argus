"""Message and tool-payload sanitization.

Port of `hermes-agent/agent/message_sanitization.py` (the parts we use).
Walks OpenAI-format message lists and structured payloads, repairing
characters that would otherwise crash `json.dumps` inside the OpenAI
SDK or be rejected by upstream APIs.

In-place mutation; returns True if anything was changed (so callers can
log a warning and increment a counter).
"""

from __future__ import annotations

import json
import re
from typing import Any


# Lone surrogate code points are invalid in UTF-8 and crash json.dumps
# inside the OpenAI SDK. Common source: copy-pasted strings from broken
# Windows clipboards, half-decoded UTF-16, malformed tool-call args.
_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def sanitize_surrogates(text: str) -> str:
    """Replace lone surrogates with U+FFFD. Fast no-op when text is clean."""
    if _SURROGATE_RE.search(text):
        return _SURROGATE_RE.sub("�", text)
    return text


def sanitize_structure_surrogates(payload: Any) -> bool:
    """Recursively scrub surrogates from dicts/lists in place. Returns True
    if anything changed (useful for logging metrics)."""
    found = False

    def walk(node: Any) -> None:
        nonlocal found
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str):
                    if _SURROGATE_RE.search(v):
                        node[k] = _SURROGATE_RE.sub("�", v)
                        found = True
                elif isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, str):
                    if _SURROGATE_RE.search(v):
                        node[i] = _SURROGATE_RE.sub("�", v)
                        found = True
                elif isinstance(v, (dict, list)):
                    walk(v)

    walk(payload)
    return found


def sanitize_messages_surrogates(messages: list) -> bool:
    """Scrub surrogates from every string field in an OpenAI message list."""
    any_changed = False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        for key in ("content", "name"):
            v = msg.get(key)
            if isinstance(v, str) and _SURROGATE_RE.search(v):
                msg[key] = _SURROGATE_RE.sub("�", v)
                any_changed = True
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else None
            if not fn:
                continue
            for key in ("name", "arguments"):
                v = fn.get(key)
                if isinstance(v, str) and _SURROGATE_RE.search(v):
                    fn[key] = _SURROGATE_RE.sub("�", v)
                    any_changed = True
        # Nested structured fields (e.g. reasoning_details on o-series).
        if sanitize_structure_surrogates(msg):
            any_changed = True
    return any_changed


# ── Tool-call JSON repair ────────────────────────────────────────────────


def looks_truncated(args_text: str) -> bool:
    """Heuristic: tool-call arguments JSON looks truncated mid-stream.

    Some routers (notably OpenRouter for certain models) rewrite
    `finish_reason` from `length` to `tool_calls` even when the model
    actually got cut off, so we have to detect this ourselves.
    """
    if not args_text:
        return False
    stripped = args_text.rstrip()
    if not stripped:
        return False
    # A well-formed args object always ends with `}` or `]`.
    return not stripped.endswith(("}", "]"))


def try_repair_tool_args(args_text: str) -> tuple[dict, bool]:
    """Parse tool-call arguments JSON, repairing common breakages.

    Returns `(parsed_dict, was_repaired)`. `was_repaired=True` is the
    caller's cue to log a warning and consider retrying.
    """
    if not args_text:
        return {}, False
    try:
        return json.loads(args_text), False
    except json.JSONDecodeError:
        pass

    # Attempt 1: close one unclosed brace/bracket.
    for closer in ("}", "]", "\"}", "\"]}"):
        try:
            return json.loads(args_text + closer), True
        except json.JSONDecodeError:
            continue

    # Attempt 2: strip a trailing comma and retry.
    stripped = args_text.rstrip().rstrip(",")
    try:
        return json.loads(stripped + "}"), True
    except json.JSONDecodeError:
        pass

    # Give up — return the raw text so the agent can complain to the
    # model and ask for a re-call.
    return {"_raw_unparseable": args_text}, True


__all__ = [
    "sanitize_surrogates",
    "sanitize_messages_surrogates",
    "sanitize_structure_surrogates",
    "looks_truncated",
    "try_repair_tool_args",
]

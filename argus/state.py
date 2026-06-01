"""Tiny config helpers and the masked-secret printer.

Used by `argus config`, the setup wizard echo, and `argus doctor`.
Centralised so the masking rule is consistent everywhere.
"""

from __future__ import annotations


def mask_secret(value: str, *, prefix_keep: int = 4, suffix_keep: int = 4) -> str:
    """`gsk_********************************3Xb2`-style masking.

    Keep first 4 (typically the provider prefix `sk-`, `gsk_`, `hf_`) and last 4
    (so the user can recognise their key). Everything in between collapses to
    a fixed-width run of `*` so two different-length keys still look uniform.
    """
    if not value:
        return ""
    if len(value) <= prefix_keep + suffix_keep:
        return "*" * len(value)
    return f"{value[:prefix_keep]}{'*' * 28}{value[-suffix_keep:]}"


def is_secret_key(name: str) -> bool:
    """Config keys whose values must be masked when printed."""
    n = name.lower()
    return any(
        token in n
        for token in ("api_key", "token", "secret", "password", "bearer")
    )


import re as _re

# Longest-first alternation so `sk-ant-` is matched as a unit and not as `sk-`.
SECRET_PATTERNS = ("gsk_", "sk-ant-", "sk-", "hf_", "Bearer ", "AIza")
_SECRET_RE = _re.compile(
    "(" + "|".join(_re.escape(p) for p in SECRET_PATTERNS) + r")[A-Za-z0-9_-]+"
)


def scrub_line(line: str) -> str:
    """Best-effort secret scrubber for log lines.

    Replaces `<prefix><payload>` with `<prefix>***` for every known
    provider-key prefix. Single regex pass, longest-first alternation.
    """
    return _SECRET_RE.sub(lambda m: m.group(1) + "***", line)

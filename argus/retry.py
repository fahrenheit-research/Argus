"""Self-healing — tenacity retry + Hermes-style jittered backoff.

Imports Hermes-Agent's `jittered_backoff` pattern verbatim (their
`agent/retry_utils.py`) so multiple sessions sharing a key don't pile
their retries onto the same instant.

Used by the provider transports (single LLM call) and the agent loop
(turn-level errors that fall through to the fallback chain).
"""

from __future__ import annotations

import logging
import random
import threading
import time

import tenacity
from tenacity import (
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
)
from tenacity.wait import wait_base

from argus.providers.base import ProviderError

log = logging.getLogger("argus.retry")


# ── Hermes-style jittered backoff ────────────────────────────────────────
# Source: nousresearch/hermes-agent · agent/retry_utils.py
# Decorrelates concurrent retries: a monotonic counter is XOR'd into the
# RNG seed so two sessions retrying at the same instant pick different
# delays.

_jitter_counter = 0
_jitter_lock = threading.Lock()


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    jitter_ratio: float = 0.5,
) -> float:
    """Compute `min(base * 2^(attempt-1), max_delay) + uniform(0, ratio*delay)`.

    `attempt` is 1-based. The RNG is seeded from `time_ns ^ counter` so
    concurrent processes don't collide.
    """
    global _jitter_counter
    with _jitter_lock:
        _jitter_counter += 1
        tick = _jitter_counter

    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2 ** exponent), max_delay)

    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    jitter = random.Random(seed).uniform(0, jitter_ratio * delay)
    return delay + jitter


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, ProviderError):
        return exc.kind in ("rate_limit", "server", "network")
    # Anything else (KeyboardInterrupt, our own bugs) — bubble up.
    return False


class _WaitJittered(wait_base):
    """Honor `retry_after` if the exception carries one; otherwise use
    decorrelated jittered backoff (Hermes pattern)."""

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, ProviderError) and exc.retry_after:
            return float(exc.retry_after)
        return jittered_backoff(retry_state.attempt_number)


def _log_before_sleep(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    log.warning(
        "retrying provider call · attempt=%d · wait=%.1fs · %s",
        retry_state.attempt_number,
        getattr(retry_state.next_action, "sleep", 0.0),
        f"{type(exc).__name__}: {exc}" if exc else "",
    )


def with_retry(max_attempts: int = 5):
    """Decorator factory. Apply to any async provider call site."""
    return tenacity.retry(
        retry=retry_if_exception(_is_retryable),
        wait=_WaitJittered(),
        stop=stop_after_attempt(max_attempts),
        before_sleep=_log_before_sleep,
        reraise=True,
    )


__all__ = ["with_retry", "jittered_backoff"]

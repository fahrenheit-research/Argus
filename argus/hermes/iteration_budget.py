"""Per-agent iteration budget — thread-safe consume/refund counter.

Port of `hermes-agent/agent/iteration_budget.py`. Verbatim semantics:
- consume() returns False instead of raising when the cap is hit
- refund() gives back an iteration (used by programmatic tool-call paths
  like `execute_code` where the model shouldn't be billed for the loop)
- thread-safe via a per-instance lock so background tool execution and
  the main loop can share one budget without races
"""

from __future__ import annotations

import threading


class IterationBudget:
    """Thread-safe iteration counter for one agent (parent or subagent)."""

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration. Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (programmatic tool paths)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


__all__ = ["IterationBudget"]

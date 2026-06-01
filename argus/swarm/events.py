"""Streaming events emitted by the Constellation coordinator.

Consumers (the chat REPL, the Telegram gateway, the demo screen) can
subscribe to these to render live progress without polling. All events
are frozen dataclasses so they're safe to fan out across tasks.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Base ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SwarmEvent:
    """Marker base; consumers can isinstance()-discriminate."""
    pass


# ── Role lifecycle ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleStarted(SwarmEvent):
    role: str            # role name, e.g. "Scout"
    task_id: str         # unique within this Constellation
    description: str     # one-line task summary
    budget_tokens: int   # pre-allocated by the Architect


@dataclass(frozen=True)
class RoleFinished(SwarmEvent):
    role: str
    task_id: str
    elapsed_ms: int
    tokens_used: int
    output_preview: str  # first 120 chars of the result


@dataclass(frozen=True)
class RoleFailed(SwarmEvent):
    role: str
    task_id: str
    error: str           # exception class + message, truncated
    will_retry: bool     # True if the Medic will get a pass


# ── Verification ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClaimRaised(SwarmEvent):
    """A Scout has produced an output claim that needs auditing."""
    task_id: str
    claim: str           # the claim text, ≤ 200 chars
    raised_by: str       # role name that produced it


@dataclass(frozen=True)
class ClaimVerdict(SwarmEvent):
    """An Auditor has ruled on a claim."""
    task_id: str
    verdict: str         # "confirmed" | "refuted" | "uncertain"
    reasoning: str       # one-sentence justification
    confidence: float    # 0.0 to 1.0


# ── Budget ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BudgetTick(SwarmEvent):
    """Periodic budget snapshot for UI; emitted every ~2s."""
    spent_tokens: int
    total_tokens: int
    elapsed_ms: int
    active_roles: int

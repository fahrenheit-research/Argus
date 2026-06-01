"""ARGUS Constellation — a self-coordinating, self-verifying, self-healing
team of role-specialised agents.

This is the agent-team primitive that drives multi-step / multi-perspective
work in ARGUS. Goes beyond Hermes' `delegate_task` and `mixture_of_agents`
on three axes:

  1. ADVERSARIAL VERIFICATION by default — every Scout claim is
     challenged by an Auditor (with a different system prompt + tool
     subset) before it can land in the Synthesizer's context. Bad
     claims are silently dropped or sent back for a second pass.

  2. SELF-HEALING via the Medic role — any sub-task that fails
     (exception, timeout, malformed output) gets exactly one Medic
     pass that diagnoses the failure and re-issues the work with
     adjusted scope. Successful repairs are recorded to AgentMomento
     as a skill so the same failure won't repeat next session.

  3. BUDGET-AWARE coordination — each Constellation runs under a
     token budget; the Architect pre-allocates per-role budgets and
     the coordinator pre-empts roles that exceed.

Public API:

    from argus.swarm import Constellation
    result = await Constellation(goal="…", cfg=cfg).run()

The whole package is async-first, fully typed, and avoids global state
(every Constellation is its own world). See `roles.py` for what each
role does and `constellation.py` for the coordination logic.
"""

from argus.swarm.constellation import Constellation, ConstellationResult
from argus.swarm.events import (
    SwarmEvent, RoleStarted, RoleFinished, RoleFailed,
    ClaimRaised, ClaimVerdict, BudgetTick,
)
from argus.swarm.roles import Role, ROLES

__all__ = [
    "Constellation", "ConstellationResult",
    "SwarmEvent", "RoleStarted", "RoleFinished", "RoleFailed",
    "ClaimRaised", "ClaimVerdict", "BudgetTick",
    "Role", "ROLES",
]

"""`constellation` tool — exposes the Constellation swarm to the agent.

Lets ARGUS hand off a complex multi-perspective task to a coordinated
team of role-specialised agents (Architect → Scouts/Engineers/Cartographers
→ Auditors → Medic → Synthesizer) instead of trying to do everything
itself in one turn.

When to call this (taught in the system prompt):
  - User wants a real research dive ("compare three vector DBs for me")
  - User wants production-grade work that benefits from verification
  - Anything where parallel exploration + adversarial check pays off

When NOT to call this:
  - One-shot factual lookups (use web_search instead)
  - Single-file edits (use patch/write_file)
  - Anything the agent can do in one round
"""

from __future__ import annotations

from typing import Any

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


async def _constellation(args: dict[str, Any]) -> str:
    """Run a Constellation and return the Synthesizer's final answer."""
    from argus.swarm import Constellation
    from argus import config as _config

    goal = (args.get("goal") or "").strip()
    if not goal:
        return "ERROR: goal required (the multi-step objective for the swarm)"

    budget = int(args.get("budget_tokens", 30_000))
    timeout = float(args.get("timeout_seconds", 240.0))
    parallelism = int(args.get("max_parallelism", 5))

    cfg = _config.load()
    constellation = Constellation(
        goal=goal,
        cfg=cfg,
        budget_tokens=budget,
        timeout_seconds=timeout,
        max_parallelism=parallelism,
    )
    result = await constellation.run()

    # Compose a short structured response: stats + the synthesized answer.
    header = (
        f"⟨◇⟩ Constellation complete — "
        f"{result.role_count} role(s), {result.tokens_used:,} tokens, "
        f"{result.elapsed_ms/1000:.1f}s\n"
        f"  Confirmed: {len(result.confirmed_outputs)}  •  "
        f"Refuted: {len(result.refuted_outputs)}  •  "
        f"Failed: {len(result.failed_outputs)}  •  "
        f"Healed: {len(result.healed_outputs)}\n"
        f"{'─' * 60}\n\n"
    )
    return header + result.answer


def register_constellation_tool() -> None:
    register(ToolImpl(
        toolset="delegation",
        spec=ToolSpec(
            name="constellation",
            description=(
                "Run a Constellation swarm: Architect decomposes the goal "
                "into parallel sub-tasks; Scouts/Engineers/Cartographers "
                "execute in parallel; Auditors adversarially verify each "
                "output; a Medic retries any failure once; a Synthesizer "
                "composes the final answer. Use this for multi-step "
                "research, comparative analysis, or any task that benefits "
                "from parallel exploration + verification."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string",
                             "description": "The user-facing goal the swarm will satisfy"},
                    "budget_tokens": {"type": "integer",
                                       "description": "Hard ceiling on total tokens. Default 30000."},
                    "timeout_seconds": {"type": "number",
                                         "description": "Hard wall-clock ceiling. Default 240."},
                    "max_parallelism": {"type": "integer",
                                         "description": "Max concurrent role tasks. Default 5."},
                },
                "required": ["goal"],
            },
        ),
        handler=_constellation,
    ))

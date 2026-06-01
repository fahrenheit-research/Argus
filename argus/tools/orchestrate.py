"""Multi-model orchestration — Argus coordinates a team of specialist agents.

Architecture (Hermes-inspired, ARGUS-native):

  User prompt
      │
      ▼
  Orchestrator (heavy model — default or configured)
      │  decomposes task into sub-tasks
      │  assigns each sub-task to the best-fit model
      ▼
  ┌─────────┬──────────┬──────────┐
  │ Planner │ Executor │ Reviewer │
  │ (heavy) │  (fast)  │ (heavy)  │
  └─────────┴──────────┴──────────┘
      │          │           │
      └──────────┴───────────┘
                 │
      Final answer synthesised

The `orchestrate` tool exposes this to the agent so the model itself can
trigger orchestration for complex tasks. Users can also type:
  /orchestrate   — interactive multi-model setup
  argus config set agent.orchestration.planner anthropic/claude-opus-4-7
  argus config set agent.orchestration.executor groq/llama-3.1-8b-instant
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from argus import config as _config
from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


# ── Orchestration config ──────────────────────────────────────────────────────


@dataclass
class OrchestratorTeam:
    """A named set of models for different roles."""
    planner:  tuple[str, str]    # (provider_id, model_id)
    executor: tuple[str, str]
    reviewer: tuple[str, str] | None = None


def _default_team(cfg) -> OrchestratorTeam:
    """Build a team from config or sensible defaults."""
    pid = cfg.agent.default_provider
    mid = cfg.agent.default_model
    fast_pid = cfg.auxiliary.extract.provider
    fast_mid = cfg.auxiliary.extract.model

    return OrchestratorTeam(
        planner=(pid, mid),
        executor=(fast_pid, fast_mid),
        reviewer=(pid, mid) if pid != fast_pid else None,
    )


# ── The orchestrate tool ──────────────────────────────────────────────────────


async def _orchestrate_tool(args: dict[str, Any]) -> str:
    """Spawn a planner + executor + optional reviewer pipeline.

    The planner breaks the task into numbered steps.
    Each step runs through the executor (fast model, with tools).
    The reviewer stitches everything together.
    """
    task = (args.get("task") or "").strip()
    if not task:
        return "ERROR: provide a 'task' string"

    cfg = _config.load()
    team = _default_team(cfg)

    # ── Phase 1: Plan ──────────────────────────────────────────────────
    from argus.loop import Conversation, TextEvent, run_turn

    plan_convo = Conversation()
    plan_parts: list[str] = []
    async for evt in run_turn(
        cfg, plan_convo,
        f"Break this task into 3-5 numbered concrete steps (be very concise): {task}",
        provider_id=team.planner[0],
        model=team.planner[1],
        max_tools=0,                 # pure reasoning, no tools in planning
    ):
        if isinstance(evt, TextEvent):
            plan_parts.append(evt.text)
    plan_text = "".join(plan_parts).strip()

    # ── Phase 2: Execute ───────────────────────────────────────────────
    exec_convo = Conversation()
    exec_parts: list[str] = []
    exec_prompt = (
        f"Original task: {task}\n\n"
        f"Plan:\n{plan_text}\n\n"
        f"Execute the plan step by step. Use tools where needed. "
        f"Show results for each step."
    )
    async for evt in run_turn(
        cfg, exec_convo, exec_prompt,
        provider_id=team.executor[0],
        model=team.executor[1],
        max_tools=6,
    ):
        if isinstance(evt, TextEvent):
            exec_parts.append(evt.text)
    exec_text = "".join(exec_parts).strip()

    # ── Phase 3: Review (optional) ─────────────────────────────────────
    if team.reviewer and args.get("review", True):
        rev_convo = Conversation()
        rev_parts: list[str] = []
        rev_prompt = (
            f"Task: {task}\n\n"
            f"Execution result:\n{exec_text}\n\n"
            f"Review: is the result complete and correct? "
            f"Give a final polished answer."
        )
        async for evt in run_turn(
            cfg, rev_convo, rev_prompt,
            provider_id=team.reviewer[0],
            model=team.reviewer[1],
            max_tools=0,
        ):
            if isinstance(evt, TextEvent):
                rev_parts.append(evt.text)
        final = "".join(rev_parts).strip()
    else:
        final = exec_text

    return (
        f"**Orchestration complete**\n\n"
        f"Models: planner={team.planner[1]} · executor={team.executor[1]}"
        + (f" · reviewer={team.reviewer[1]}" if team.reviewer else "")
        + f"\n\n**Plan:**\n{plan_text}\n\n**Result:**\n{final}"
    )


def register_orchestrate_tool() -> None:
    register(ToolImpl(
        toolset="delegation",
        spec=ToolSpec(
            name="orchestrate",
            description=(
                "Run a multi-model orchestration pipeline: planner model breaks the task, "
                "executor model (fast) runs tool calls, reviewer model synthesises. "
                "Use for complex multi-step tasks that benefit from multiple model strengths."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task":   {"type": "string", "description": "The full task to orchestrate"},
                    "review": {"type": "boolean", "description": "Run a reviewer pass (default true)"},
                },
                "required": ["task"],
            },
        ),
        handler=_orchestrate_tool,
    ))

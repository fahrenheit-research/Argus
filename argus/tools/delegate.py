"""`delegate_task` tool — spawn a sub-agent to handle a focused task.

Hermes pattern (`hermes-agent/agent/delegation.py`): the parent calls
`delegate_task` with a self-contained prompt + optional toolset
restriction. We spin up a fresh `Conversation`, a smaller
`IterationBudget` (cap 30 vs parent's 60), and run the agent loop to
completion. The child's final assistant text becomes the tool result.

Multi-model: the parent can pin the child to a CHEAPER model for
focused work (e.g. "summarise this", "extract URLs") via the `model`
parameter, falling back to the configured auxiliary.extract model if
unspecified. This is how ARGUS gets multi-model behaviour without
exposing transport selection to the LLM.
"""

from __future__ import annotations

import asyncio
from typing import Any

from argus import config as _config
from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


async def _delegate_task(args: dict[str, Any]) -> str:
    prompt = (args.get("task") or "").strip()
    if not prompt:
        return "ERROR: provide a non-empty 'task' string"

    toolsets_arg = args.get("toolsets")
    requested_model = (args.get("model") or "").strip()

    cfg = _config.load()

    # Toolset restriction: child gets a SUBSET of parent's toolsets.
    if isinstance(toolsets_arg, list) and toolsets_arg:
        child_toolsets = [t for t in toolsets_arg if t in cfg.agent.toolsets]
    else:
        # Default: drop `shell` (children shouldn't run commands without
        # the user seeing the propose+approve dance) and drop `delegation`
        # itself (one level of nesting only — Hermes does the same).
        child_toolsets = [t for t in cfg.agent.toolsets if t not in ("shell", "delegation")]

    # Multi-model: pick the model. Order of preference:
    #   1. explicit `model` from the parent's tool call
    #   2. configured auxiliary.extract model (cheap-and-fast default)
    #   3. parent's default
    if requested_model:
        child_provider = cfg.agent.default_provider
        child_model = requested_model
    elif cfg.auxiliary.extract.model:
        child_provider = cfg.auxiliary.extract.provider
        child_model = cfg.auxiliary.extract.model
    else:
        child_provider = cfg.agent.default_provider
        child_model = cfg.agent.default_model

    # Build a temporarily-mutated config so the child only sees its toolsets.
    import copy
    child_cfg = copy.copy(cfg)
    child_cfg.agent = copy.copy(cfg.agent)
    child_cfg.agent.toolsets = child_toolsets

    # Lazy import to dodge a circular dependency (registry ← graph/delegate
    # is registered at startup; loop imports registry; loop also needs to
    # be importable from delegate).
    from argus.loop import (
        Conversation,
        FinishEvent,
        Message,
        TextEvent,
        ToolCallEvent,
        ToolResultEvent,
        run_turn,
    )

    child_convo = Conversation()
    # Give the child a focused system prompt — it's a worker, not a chat.
    child_convo.add(Message(
        role="system",
        content=(
            "You are an ARGUS sub-agent dispatched by the main agent for a "
            "single focused task. Do the task, then return ONLY the result. "
            "Do not greet the caller, do not narrate your reasoning, do not "
            "ask clarifying questions — make the most reasonable assumption "
            "and proceed."
        ),
    ))

    out: list[str] = []
    tool_events: list[str] = []

    async def drive() -> None:
        async for evt in run_turn(child_cfg, child_convo, prompt,
                                   provider_id=child_provider, model=child_model):
            if isinstance(evt, TextEvent):
                out.append(evt.text)
            elif isinstance(evt, ToolCallEvent):
                tool_events.append(f"  ⤷ {evt.name}({', '.join(evt.arguments)[:60]})")
            elif isinstance(evt, ToolResultEvent):
                pass  # we don't need the per-tool stdout in the parent
            elif isinstance(evt, FinishEvent):
                pass

    try:
        await asyncio.wait_for(drive(), timeout=90)
    except asyncio.TimeoutError:
        return "ERROR: sub-agent timed out after 90s"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: sub-agent crashed: {type(e).__name__}: {e}"

    final = "".join(out).strip() or "(sub-agent returned no text)"
    if tool_events:
        return f"{final}\n\n--- tool trace ---\n" + "\n".join(tool_events[:5])
    return final


def register_delegate_tool() -> None:
    register(ToolImpl(
        toolset="delegation",
        spec=ToolSpec(
            name="delegate_task",
            description=(
                "Spawn a sub-agent to handle a focused, self-contained task and "
                "return its final answer. Use for: long fetch/extract/summarise "
                "pipelines, parallel research, anything that would clutter the "
                "main conversation. The sub-agent has its own conversation, "
                "budget, and (optionally) a cheaper model. ONE level of nesting "
                "only — sub-agents cannot delegate further."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Self-contained prompt for the sub-agent. Must be answerable without your conversation history.",
                    },
                    "toolsets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: restrict the child to these toolsets (default: memory, web, files).",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional: model id for the child (default: cfg.auxiliary.extract.model — cheap & fast).",
                    },
                },
                "required": ["task"],
            },
        ),
        handler=_delegate_task,
    ))

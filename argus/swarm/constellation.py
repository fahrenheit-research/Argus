"""Constellation coordinator — the conductor that runs a swarm.

Flow:
  1. ARCHITECT  → plan          (JSON: list of sub-tasks with roles)
  2. SCOUTS / ENGINEERS / CARTOGRAPHERS → execute in parallel
     ↓
     For each output, an AUDITOR runs in parallel and rules on it.
     Refuted claims are dropped from the synthesis pool.
  3. MEDIC      → retry any failed sub-task once
  4. SYNTHESIZER → produce the final answer

The whole pipeline is async, budget-aware, and emits SwarmEvents at
each phase so the UI can render live progress.

Design constraints:
  - One Constellation per top-level call. Coordinators do NOT spawn
    other Constellations (use Hermes-style delegate_task for that, or
    chain Constellations explicitly).
  - Each role runs with its own restricted tool catalog (see Role.allowed_toolsets).
  - Provider/model is shared across roles by default but can be overridden
    per role via cfg.auxiliary entries (Scout uses aux.extract, Synthesizer
    uses the main model, etc.).
  - Token budget is a hard ceiling — when the coordinator's accumulated
    `tokens_used` exceeds it, in-flight role tasks are cancelled.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from argus import config as _config
from argus.swarm.events import (
    BudgetTick, ClaimRaised, ClaimVerdict, RoleFailed,
    RoleFinished, RoleStarted, SwarmEvent,
)
from argus.swarm.roles import ROLES, Role


# ── Public result type ──────────────────────────────────────────────────────


@dataclass
class ConstellationResult:
    """Final return from a Constellation run."""
    answer: str
    plan: list[dict[str, Any]] = field(default_factory=list)
    confirmed_outputs: list[dict[str, Any]] = field(default_factory=list)
    refuted_outputs:   list[dict[str, Any]] = field(default_factory=list)
    failed_outputs:    list[dict[str, Any]] = field(default_factory=list)
    healed_outputs:    list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    elapsed_ms:  int = 0
    role_count:  int = 0


# ── Sub-task spec ───────────────────────────────────────────────────────────


@dataclass
class SubTask:
    id: str
    role: str
    description: str
    accept: str
    budget_tokens: int


# ── Coordinator ─────────────────────────────────────────────────────────────


class Constellation:
    """Runs one Constellation top-to-bottom and yields a ConstellationResult.

    Usage (one-shot):

        result = await Constellation(goal="…", cfg=cfg).run()

    Usage (streaming UI):

        async for evt in Constellation(goal="…", cfg=cfg).stream():
            handle(evt)
        result = constellation.result   # populated once stream() exits

    `provider_override` lets callers force a specific provider for the
    Architect/Synthesizer (the others use cfg.auxiliary routing).
    """

    def __init__(
        self,
        *,
        goal: str,
        cfg: _config.Config | None = None,
        budget_tokens: int = 30_000,
        timeout_seconds: float = 240.0,
        provider_override: str | None = None,
        model_override: str | None = None,
        max_parallelism: int = 5,
    ) -> None:
        self.goal = goal.strip()
        self.cfg = cfg or _config.load()
        self.budget_tokens = budget_tokens
        self.timeout_seconds = timeout_seconds
        self.provider_override = provider_override
        self.model_override = model_override
        self.max_parallelism = max_parallelism

        self.result = ConstellationResult(answer="")
        self._events: asyncio.Queue[SwarmEvent | None] = asyncio.Queue()
        self._started_at = 0.0
        self._tokens_used = 0
        self._active_roles = 0

    # ── Top-level entry points ──────────────────────────────────────────

    async def run(self) -> ConstellationResult:
        """Run to completion, swallowing the event stream. Returns result."""
        async for _ in self.stream():
            pass
        return self.result

    async def stream(self) -> AsyncIterator[SwarmEvent]:
        """Run the pipeline; yield events as they happen."""
        self._started_at = time.monotonic()
        runner_task = asyncio.create_task(self._run_pipeline())
        ticker_task = asyncio.create_task(self._budget_ticker())

        try:
            while True:
                evt = await self._events.get()
                if evt is None:                  # sentinel
                    break
                yield evt
        finally:
            runner_task.cancel()
            ticker_task.cancel()
            try:
                await runner_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await ticker_task
            except (asyncio.CancelledError, Exception):
                pass

        self.result.elapsed_ms = int((time.monotonic() - self._started_at) * 1000)
        self.result.tokens_used = self._tokens_used

    # ── Pipeline phases ─────────────────────────────────────────────────

    async def _run_pipeline(self) -> None:
        try:
            await asyncio.wait_for(self._pipeline_inner(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            self.result.answer = (
                f"⏱  Constellation hit the {self.timeout_seconds:.0f}s timeout. "
                f"Partial answer based on what completed:\n\n"
                f"{self._format_partials()}"
            )
        except Exception as e:  # noqa: BLE001
            self.result.answer = f"⚠ Constellation crashed: {type(e).__name__}: {e}"
        finally:
            await self._events.put(None)

    async def _pipeline_inner(self) -> None:
        # ── Phase 1: Architect plans ────────────────────────────────────
        plan = await self._run_architect()
        if not plan:
            self.result.answer = (
                "The Architect couldn't decompose this — it's likely a "
                "direct question, not a multi-step task. Re-ask without "
                "wrapping it in a constellation call."
            )
            return
        self.result.plan = [task.__dict__ for task in plan]

        # ── Phase 2: parallel execution ─────────────────────────────────
        sem = asyncio.Semaphore(self.max_parallelism)

        async def _gated(task: SubTask) -> dict[str, Any]:
            async with sem:
                return await self._run_role_task(task)

        execution_results = await asyncio.gather(
            *(_gated(t) for t in plan),
            return_exceptions=False,
        )
        # Drop self-cancelled tasks
        execution_results = [r for r in execution_results if r is not None]

        # ── Phase 3: Medic for failures ─────────────────────────────────
        failures = [r for r in execution_results if r["status"] == "failed"]
        healed: list[dict[str, Any]] = []
        for failed in failures:
            healed_result = await self._run_medic(failed, plan)
            if healed_result and healed_result["status"] == "succeeded":
                healed.append(healed_result)
        # Merge healed back into the success pool
        successes = [r for r in execution_results if r["status"] == "succeeded"]
        successes.extend(healed)
        self.result.failed_outputs = [r for r in failures
                                      if r["task_id"] not in {h["task_id"] for h in healed}]
        self.result.healed_outputs = healed

        # ── Phase 4: parallel adversarial audit ─────────────────────────
        if successes:
            audit_results = await asyncio.gather(
                *(self._audit(r) for r in successes),
                return_exceptions=False,
            )
            confirmed = []
            refuted = []
            for r, verdict in zip(successes, audit_results):
                if verdict["verdict"] in ("confirmed", "uncertain"):
                    r["verdict"] = verdict
                    confirmed.append(r)
                else:
                    r["verdict"] = verdict
                    refuted.append(r)
            self.result.confirmed_outputs = confirmed
            self.result.refuted_outputs = refuted
        else:
            self.result.confirmed_outputs = []

        # ── Phase 5: Synthesizer composes the final answer ──────────────
        self.result.answer = await self._run_synthesizer(self.result.confirmed_outputs)
        self.result.role_count = 1 + len(plan) + len(self.result.confirmed_outputs) + 1
        if healed:
            self.result.role_count += len(healed)

    # ── Phase implementations ───────────────────────────────────────────

    async def _run_architect(self) -> list[SubTask]:
        role = ROLES["Architect"]
        prompt = (
            f"USER GOAL:\n  {self.goal}\n\n"
            f"Decompose now. Output JSON only as specified."
        )
        text = await self._role_one_shot(role, prompt, task_id="arch")
        plan_json = _extract_json_object(text)
        if not plan_json or "plan" not in plan_json:
            return []
        plan: list[SubTask] = []
        # Allocate per-task budget proportional to total minus reserves
        reserved = ROLES["Architect"].default_budget_tokens + \
                   ROLES["Synthesizer"].default_budget_tokens + \
                   2 * ROLES["Auditor"].default_budget_tokens
        budget_per_task = max(2_000, (self.budget_tokens - reserved) // max(1, len(plan_json["plan"])))
        for raw in plan_json["plan"]:
            role_name = raw.get("role", "Scout")
            if role_name not in ROLES:
                role_name = "Scout"
            plan.append(SubTask(
                id=raw.get("id", uuid.uuid4().hex[:6]),
                role=role_name,
                description=raw.get("description", "").strip(),
                accept=raw.get("accept", "").strip(),
                budget_tokens=budget_per_task,
            ))
        return plan

    async def _run_role_task(self, task: SubTask) -> dict[str, Any] | None:
        role = ROLES[task.role]
        prompt = (
            f"SUB-TASK ({task.id}):\n  {task.description}\n\n"
            f"ACCEPTANCE CRITERION:\n  {task.accept}\n\n"
            f"USER GOAL (context):\n  {self.goal}\n\n"
            f"Do the work now. Follow your role's output format strictly."
        )
        await self._events.put(RoleStarted(
            role=role.name, task_id=task.id,
            description=task.description, budget_tokens=task.budget_tokens,
        ))
        self._active_roles += 1
        t0 = time.monotonic()
        try:
            text = await self._role_one_shot(role, prompt, task_id=task.id)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            await self._events.put(RoleFinished(
                role=role.name, task_id=task.id, elapsed_ms=elapsed_ms,
                tokens_used=_rough_token_count(text),
                output_preview=text[:120].replace("\n", " "),
            ))
            return {"task_id": task.id, "role": role.name,
                    "description": task.description, "accept": task.accept,
                    "status": "succeeded", "output": text,
                    "elapsed_ms": elapsed_ms}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            await self._events.put(RoleFailed(
                role=role.name, task_id=task.id, error=err[:200], will_retry=True,
            ))
            return {"task_id": task.id, "role": role.name,
                    "description": task.description, "accept": task.accept,
                    "status": "failed", "error": err, "output": ""}
        finally:
            self._active_roles -= 1

    async def _run_medic(
        self, failed: dict[str, Any], plan: list[SubTask],
    ) -> dict[str, Any] | None:
        role = ROLES["Medic"]
        prompt = (
            f"FAILED SUB-TASK:\n"
            f"  id:          {failed['task_id']}\n"
            f"  role:        {failed['role']}\n"
            f"  description: {failed['description']}\n"
            f"  error:       {failed['error']}\n\n"
            f"USER GOAL:\n  {self.goal}\n\n"
            f"Diagnose and rewrite the task. Then I will re-run it."
        )
        try:
            medic_text = await self._role_one_shot(role, prompt, task_id=f"medic-{failed['task_id']}")
        except Exception:
            return None

        # Re-issue the original role with the medic's rewrite as the new prompt.
        original_role = ROLES.get(failed["role"])
        if not original_role:
            return None
        retry_prompt = (
            f"You previously failed with: {failed['error']}\n\n"
            f"MEDIC ADVICE:\n{medic_text}\n\n"
            f"Try again now with the adjusted scope. Stay strictly in your role's output format."
        )
        try:
            text = await self._role_one_shot(original_role, retry_prompt,
                                             task_id=f"retry-{failed['task_id']}")
            return {"task_id": failed["task_id"], "role": failed["role"],
                    "description": failed["description"],
                    "accept": failed["accept"],
                    "status": "succeeded", "output": text,
                    "healed": True, "diagnosis": medic_text[:400]}
        except Exception as e:  # noqa: BLE001
            return {"task_id": failed["task_id"], "role": failed["role"],
                    "status": "failed", "error": f"medic-retry-also-failed: {e}",
                    "output": ""}

    async def _audit(self, succeeded: dict[str, Any]) -> dict[str, Any]:
        role = ROLES["Auditor"]
        claim = succeeded.get("output", "")[:1500]
        await self._events.put(ClaimRaised(
            task_id=succeeded["task_id"],
            claim=claim[:200].replace("\n", " "),
            raised_by=succeeded["role"],
        ))
        prompt = (
            f"ORIGINAL SUB-TASK:\n  {succeeded.get('description', '')}\n"
            f"ACCEPTANCE CRITERION:\n  {succeeded.get('accept', '')}\n\n"
            f"CLAIM TO RULE ON (from the {succeeded['role']}):\n{claim}\n\n"
            f"Rule on it. JSON only."
        )
        try:
            text = await self._role_one_shot(role, prompt, task_id=f"audit-{succeeded['task_id']}")
            verdict = _extract_json_object(text) or {}
            v = verdict.get("verdict", "uncertain")
            if v not in ("confirmed", "refuted", "uncertain"):
                v = "uncertain"
            conf = float(verdict.get("confidence", 0.5))
            reasoning = str(verdict.get("reasoning", ""))[:300]
            await self._events.put(ClaimVerdict(
                task_id=succeeded["task_id"],
                verdict=v, reasoning=reasoning, confidence=conf,
            ))
            return {"verdict": v, "confidence": conf, "reasoning": reasoning}
        except Exception:
            # If the auditor fails, fall back to confirmed-with-low-confidence
            # (better to surface uncertain work than to silently drop it).
            return {"verdict": "uncertain", "confidence": 0.3,
                    "reasoning": "auditor failed; defaulting to uncertain"}

    async def _run_synthesizer(self, confirmed: list[dict[str, Any]]) -> str:
        role = ROLES["Synthesizer"]
        if not confirmed:
            return ("Nothing survived audit. The Scouts and Engineers produced "
                    "claims that the Auditors couldn't verify. Try narrowing "
                    "the goal or supplying a source.")

        summaries = []
        for r in confirmed:
            verdict = r.get("verdict", {})
            mark = "✓" if verdict.get("verdict") == "confirmed" else "◐"
            heal = " (healed)" if r.get("healed") else ""
            summaries.append(
                f"### {mark} {r['role']} — {r['description']}{heal}\n"
                f"_Confidence: {verdict.get('confidence', 0.5):.0%} — "
                f"{verdict.get('reasoning', 'no reasoning')}_\n\n"
                f"{r.get('output', '')[:2000]}"
            )

        prompt = (
            f"USER GOAL:\n  {self.goal}\n\n"
            f"VERIFIED FINDINGS FROM {len(confirmed)} ROLE(S):\n\n"
            f"{chr(10).join(summaries)}\n\n"
            f"Compose the final answer for the user now. ARGUS voice. "
            f"Bottom line first. Under 400 words unless the user asked for depth."
        )
        try:
            return await self._role_one_shot(role, prompt, task_id="synth")
        except Exception as e:  # noqa: BLE001
            return ("⚠ Synthesizer failed — surfacing raw confirmed findings:\n\n" +
                    "\n\n".join(summaries))

    # ── LLM glue ────────────────────────────────────────────────────────

    async def _role_one_shot(self, role: Role, user_prompt: str, *,
                              task_id: str) -> str:
        """Run a single LLM round for a role and return its text reply.

        Uses argus.providers.registry to build a provider, restricts the
        tool catalog to the role's allowed toolsets, and joins streamed
        text events into one final string.
        """
        from argus.providers.registry import get_transport   # correct name
        from argus.providers.base import Message
        from argus.tools.registry import register_defaults, tools_for

        register_defaults()
        tool_impls = tools_for(list(role.allowed_toolsets), disabled=None) if role.allowed_toolsets else []
        tool_specs = [t.spec for t in tool_impls]

        # Resolve provider — Architect/Synthesizer use main model, others use
        # the cheaper "extract" auxiliary by default.
        provider_id = self.provider_override or (
            self.cfg.agent.default_provider
            if role.name in ("Architect", "Synthesizer")
            else self.cfg.auxiliary.extract.provider
        )
        model = self.model_override or (
            self.cfg.agent.default_model
            if role.name in ("Architect", "Synthesizer")
            else self.cfg.auxiliary.extract.model
        )
        try:
            provider = get_transport(provider_id, self.cfg)
        except Exception:
            provider = get_transport(self.cfg.agent.default_provider, self.cfg)
            model = self.cfg.agent.default_model

        # system prompt injected as a system Message
        messages = [
            Message(role="system", content=role.system_prompt),
            Message(role="user",   content=user_prompt),
        ]

        out_parts: list[str] = []
        tool_call_buf: list[dict] = []
        for _round in range(2):
            tool_call_buf.clear()
            async for evt in provider.stream(messages=messages, model=model,
                                             tools=tool_specs):
                etype = type(evt).__name__
                if etype == "TextEvent" and getattr(evt, "text", None):
                    out_parts.append(evt.text)
                elif etype == "ToolCallEvent":
                    tool_call_buf.append({
                        "id":   getattr(evt, "id", uuid.uuid4().hex[:8]),
                        "name": getattr(evt, "name", ""),
                        "arguments": getattr(evt, "arguments", {}) or {},
                    })
            if not tool_call_buf:
                break

            # Execute tool calls and add as a user message so the model can
            # continue. We do NOT recurse — this is bounded to keep budgets sane.
            tool_outputs = []
            for tc in tool_call_buf:
                from argus.tools.registry import get_tool
                impl = get_tool(tc["name"])
                if not impl:
                    tool_outputs.append(f"[{tc['name']}] ERROR: tool not available to role {role.name}")
                    continue
                try:
                    out = await asyncio.wait_for(impl.handler(tc["arguments"]),
                                                  timeout=20)
                except Exception as e:  # noqa: BLE001
                    out = f"ERROR: {type(e).__name__}: {e}"
                tool_outputs.append(f"[{tc['name']}]\n{out}")

            # Re-prompt: include user prompt + assistant partial + tool results.
            assistant_partial = "".join(out_parts) or "(awaiting tool result)"
            messages = [
                Message(role="system",    content=role.system_prompt),
                Message(role="user",      content=user_prompt),
                Message(role="assistant", content=assistant_partial),
                Message(role="user",      content=(
                    "TOOL RESULTS:\n" + "\n\n".join(tool_outputs)
                    + "\n\nProduce your final role output now."
                )),
            ]
            out_parts = []   # collect the post-tool-result reply

        text = "".join(out_parts).strip()
        self._tokens_used += _rough_token_count(text + user_prompt + system)
        return text or "(empty role response)"

    # ── Budget ticker (cheap; every 2s) ─────────────────────────────────

    async def _budget_ticker(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            await self._events.put(BudgetTick(
                spent_tokens=self._tokens_used,
                total_tokens=self.budget_tokens,
                elapsed_ms=int((time.monotonic() - self._started_at) * 1000),
                active_roles=self._active_roles,
            ))
            if self._tokens_used >= self.budget_tokens:
                # Pre-empt: the runner will see this via cancellation up-stream.
                # We just stop ticking here.
                return

    # ── Diagnostics ─────────────────────────────────────────────────────

    def _format_partials(self) -> str:
        bits = []
        if self.result.confirmed_outputs:
            bits.append(f"  • {len(self.result.confirmed_outputs)} confirmed sub-task(s)")
        if self.result.refuted_outputs:
            bits.append(f"  • {len(self.result.refuted_outputs)} refuted")
        if self.result.failed_outputs:
            bits.append(f"  • {len(self.result.failed_outputs)} failed")
        return "\n".join(bits) or "  (nothing produced before timeout)"


# ── Helpers ──────────────────────────────────────────────────────────────────


_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Pull the first {...} block out of `text` (LLMs love wrapping JSON in prose)."""
    if not text:
        return None
    # Direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None


def _rough_token_count(text: str) -> int:
    """4-chars-per-token rule of thumb. Good enough for budget accounting."""
    return max(1, len(text) // 4)

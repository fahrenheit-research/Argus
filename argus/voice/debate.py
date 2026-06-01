"""Multi-agent voice debate — give each Constellation role its own voice.

WHY this module exists:
  When ARGUS spawns a Constellation (Architect → Scouts → Auditors →
  Synthesizer), the user currently sees text-only progress. The spec
  envisions a HEARD experience:

      User:  Hey Argus
      Argus: Listening.
      User:  Research the latest AI agent frameworks.
      ORION (Scout):     "Researching."
      NOVA  (Auditor):   "Validating."
      ATLAS (Cartographer): "Mapping connections."
      Argus: (final synthesised reply, in his own voice)

  Each role gets a distinct TTS voice so the user can hear the swarm
  thinking in parallel.

How it works:
  • Subscribes to SwarmEvent stream from argus.swarm.Constellation
  • For each RoleStarted/RoleFinished event, queues a one-line
    announcement ("Scout-2 is trawling docs") synthesised in that
    role's assigned voice
  • Queue runs serially through a single audio sink so two roles never
    speak over each other
  • Final SYNTHESIZER output is spoken in ARGUS's own voice (Supertonic
    default) at the end of the run

Voice assignment (Supertonic M1-M5 + F1-F5 give us 10 distinct voices;
we use 7 — one per swarm role):

    Architect    F1   (poised, planning)
    Scout        M1   (curious, brisk)
    Engineer     M3   (deliberate, technical)
    Cartographer F3   (warm, narrative)
    Auditor      M5   (sharp, sceptical)
    Medic        F5   (calm, reassuring)
    Synthesizer  M1   (ARGUS himself — matches CLI default)

Public API:

    debate = VoiceDebate(announce_arrivals=True, announce_completions=True)
    async for evt in constellation.stream():
        await debate.handle_event(evt)
    await debate.speak_final(constellation.result.answer)
    await debate.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

log = logging.getLogger("argus.voice.debate")


# Role -> Supertonic voice ID. M1-M5 male, F1-F5 female.
ROLE_VOICES: dict[str, str] = {
    "Architect":    "F1",
    "Scout":        "M1",
    "Engineer":     "M3",
    "Cartographer": "F3",
    "Auditor":      "M5",
    "Medic":        "F5",
    "Synthesizer":  "M1",
    # New specialist roles from the latest plan
    "Scribe":       "F1",
    "Ledger":       "M3",
    "Atelier":      "F3",
    "Herald":       "M5",
    "Analyst":      "M1",
    "Sentinel":     "M5",
    "Momento":      "F5",
}

# Short announcement lines per role (kept under 8 words so TTS is snappy)
ROLE_VERBS: dict[str, str] = {
    "Architect":    "Architecting the plan.",
    "Scout":        "Scouting the perimeter.",
    "Engineer":     "Engineering the fix.",
    "Cartographer": "Mapping the territory.",
    "Auditor":      "Auditing the claim.",
    "Medic":        "Healing the failure.",
    "Synthesizer":  "Composing the answer.",
    "Scribe":       "Drafting the document.",
    "Ledger":       "Tallying the numbers.",
    "Atelier":      "Designing the interface.",
    "Herald":       "Composing the message.",
    "Analyst":      "Crunching the data.",
    "Sentinel":     "Scanning for threats.",
    "Momento":      "Consulting memory.",
}


class VoiceDebate:
    """Serial TTS queue that gives each swarm role its own voice.

    Uses argus.voice.synthesize under the hood; falls through to silent
    no-op if Supertonic isn't installed.
    """

    def __init__(
        self,
        *,
        announce_arrivals: bool = True,
        announce_completions: bool = False,
        max_concurrent: int = 1,       # serial by design — no talk-over
    ):
        self.announce_arrivals = announce_arrivals
        self.announce_completions = announce_completions
        self._queue: asyncio.Queue[Optional[tuple[str, str]]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._sem = asyncio.Semaphore(max_concurrent)
        self._spoken_roles: set[str] = set()
        self._started = False

    # ── lifecycle ─────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._worker_task = asyncio.create_task(self._worker())

    async def shutdown(self) -> None:
        await self._queue.put(None)         # sentinel
        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=15.0)
            except asyncio.TimeoutError:
                self._worker_task.cancel()
        self._started = False

    # ── event handlers ────────────────────────────────────────────

    async def handle_event(self, evt: Any) -> None:
        """Subscribe this to Constellation.stream(). Quietly ignores
        events that aren't role lifecycle (BudgetTick, ClaimRaised, …)."""
        if not self._started:
            self.start()

        cls = type(evt).__name__
        role = getattr(evt, "role", None)
        if not role:
            return

        if cls == "RoleStarted" and self.announce_arrivals:
            # Only announce the FIRST time each role wakes — subsequent
            # sub-tasks for the same role would spam.
            if role in self._spoken_roles:
                return
            self._spoken_roles.add(role)
            verb = ROLE_VERBS.get(role, f"{role} online.")
            voice = ROLE_VOICES.get(role, "M1")
            await self._queue.put((verb, voice))

        elif cls == "RoleFinished" and self.announce_completions:
            voice = ROLE_VOICES.get(role, "M1")
            await self._queue.put((f"{role} done.", voice))

    async def speak_final(self, text: str, *, voice: str = "M1") -> None:
        """Synthesise the final SYNTHESIZER output in ARGUS's voice.
        Waits for any queued role announcements to finish first."""
        if not self._started:
            self.start()
        # Trim to ~3 sentences so the spoken summary stays under 30 s
        snippet = _to_speakable(text, max_chars=350)
        await self._queue.put((snippet, voice))
        # Drain
        await self._queue.put(None)
        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=60.0)
            except asyncio.TimeoutError:
                pass
        self._started = False

    # ── worker ────────────────────────────────────────────────────

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            text, voice = item
            try:
                async with self._sem:
                    await self._speak_one(text, voice)
            except Exception as e:  # noqa: BLE001
                log.debug("voice debate speak failed: %s", e)

    async def _speak_one(self, text: str, voice: str) -> None:
        """Synth + play a single line through the local speaker."""
        try:
            from argus.voice import synthesize
            from argus import config as _config
        except Exception:
            return

        cfg = _config.load()
        if cfg.voice.tts_provider == "none":
            return
        result = await synthesize(text, cfg=cfg, voice=voice, output_format="mp3")
        if not result or not result.path.exists():
            return

        import shutil, subprocess
        player = (
            ["afplay"] if shutil.which("afplay") else
            ["aplay", "-q"] if shutil.which("aplay") else
            ["paplay"] if shutil.which("paplay") else
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if shutil.which("ffplay") else None
        )
        if not player:
            return

        def _play():
            try:
                subprocess.run(player + [str(result.path)],
                               capture_output=True, timeout=60)
            except Exception:
                pass
        await asyncio.to_thread(_play)


def _to_speakable(text: str, *, max_chars: int = 350) -> str:
    """Strip markdown + em-dashes + URLs from text intended for TTS."""
    try:
        from argus.render import clean_text
        text = clean_text(text)
    except Exception:
        pass
    # Drop markdown syntax characters so the voice doesn't read them
    import re
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"https?://\S+", "(link)", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        # End on a sentence boundary if possible
        snipped = text[:max_chars]
        for sep in (". ", "? ", "! "):
            last = snipped.rfind(sep)
            if last > max_chars // 2:
                return snipped[: last + 1]
        return snipped + "..."
    return text

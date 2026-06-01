"""ARGUS voice listener — unified CLI, streaming TTS, interruptible.

Architecture (5 improvements over v2):

  1. UNIFIED CLI INTERFACE
     Voice runs as a background asyncio task INSIDE the existing chat REPL.
     The user can type while ARGUS is listening or speaking. Voice status
     shows in the bottom toolbar alongside ctx/ttft. Both typed and spoken
     input share the same agent conversation.

  2. DEEP MALE VOICE
     Default: en-US-GuyNeural (Edge TTS, free, no API key, warm deep male).
     Fallback chain: en-US-DavisNeural -> Supertonic M3 -> edge generic.

  3. SENTENCE-STREAMING TTS
     As the LLM streams tokens, sentence boundaries are detected in real
     time. The first sentence starts synthesizing immediately -- the user
     hears ARGUS start speaking within ~500ms of the first period, while
     subsequent sentences queue in the background. Eliminates the full
     LLM wait before audio starts.

  4. INTERRUPT WITH DEBOUNCE
     The playback monitor requires 3 consecutive frames of speech (240ms)
     before killing the player -- ambient noise, coughs, and table bumps
     no longer cut ARGUS off. Real speech still interrupts within 250ms.

  5. FULL TEXT, NO TRUNCATION
     The display and TTS both use the complete agent reply. The system
     prompt no longer emits "Short answer:" openers.

Usage (from within the `argus` chat REPL):
    > argus talk          -- starts voice; shows status in toolbar
    > /talk               -- same
    > End Argus           -- spoken close phrase
    Ctrl-C                -- immediate exit
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

# ── Voice HUD socket ──────────────────────────────────────────────────────────
# The HUD terminal window connects here to receive live events.
_SOCKET_PATH = str(Path.home() / ".argus" / "voice.sock")

# Active HUD client writers (one per connected HUD window)
_hud_writers: list[asyncio.StreamWriter] = []

# Tracks whether any HUD window has ever connected this session.
# Used to trigger voice stop when the LAST window closes.
_hud_ever_connected: bool = False

# Registered stop callback — set by run_voice_background so the socket
# handler can signal the voice loop to stop when the HUD window closes.
_hud_close_callback: Optional[Any] = None   # callable[[], None]

# Module-level handle to the currently-playing TTS player. Set by
# speak_streaming() while audio is in flight, cleared when done. The HUD
# close callback uses this to KILL audio immediately on popup close (the
# stop_flag check inside the consumer loop has up to 200ms latency).
_active_player_lock = None   # threading.Lock, lazily created
_active_player_ref:  dict = {"p": None}


def _kill_active_player() -> None:
    """Forcibly stop any TTS audio currently playing."""
    p = _active_player_ref.get("p")
    if p:
        try: p.kill()
        except Exception: pass
        _active_player_ref["p"] = None


def register_hud_close_callback(cb) -> None:
    """Register a function to call when the last HUD window disconnects.
    The voice loop uses this so closing the popup stops the mic."""
    global _hud_close_callback
    _hud_close_callback = cb


async def _start_socket_server() -> asyncio.Server | None:
    """Open a Unix domain socket so the HUD terminal can connect.

    When the LAST connected HUD window closes (user closes the popup),
    we call `_hud_close_callback()` to stop the voice loop. This makes
    closing the popup the canonical way to end a voice session.
    """
    global _hud_ever_connected
    _hud_ever_connected = False

    try:
        sock_path = Path(_SOCKET_PATH)
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        if sock_path.exists():
            sock_path.unlink()

        async def _handle(reader, writer):
            global _hud_ever_connected
            _hud_ever_connected = True
            _hud_writers.append(writer)
            log.info("HUD window connected (%d total)", len(_hud_writers))
            try:
                # Block until the HUD window closes (EOF = window closed)
                while True:
                    data = await reader.read(64)
                    if not data:
                        break
            except Exception:
                pass
            finally:
                if writer in _hud_writers:
                    _hud_writers.remove(writer)
                try: writer.close()
                except Exception: pass
                log.info("HUD window disconnected (%d remaining)", len(_hud_writers))
                # If the last HUD window just closed, stop the voice loop
                if _hud_ever_connected and not _hud_writers:
                    log.info("Last HUD window closed — stopping voice loop")
                    cb = _hud_close_callback
                    if cb:
                        try: cb()
                        except Exception: pass

        return await asyncio.start_unix_server(_handle, path=_SOCKET_PATH)
    except Exception as e:
        log.debug("voice socket server failed to start: %s", e)
        return None


def _hud_send(event: dict) -> None:
    """Fire-and-forget: send a JSON event to every connected HUD window."""
    if not _hud_writers:
        return
    payload = (json.dumps(event) + "\n").encode()
    dead: list[asyncio.StreamWriter] = []
    for w in list(_hud_writers):
        try:
            w.write(payload)
        except Exception:
            dead.append(w)
    for w in dead:
        if w in _hud_writers:
            _hud_writers.remove(w)

log = logging.getLogger("argus.voice.listener")

# Suppress the spammy asyncio warning emitted when the HUD window closes
# mid-write. The disconnect is handled cleanly by our EOF reader; the
# warning adds nothing but noise to the user's terminal.
class _SilenceAsyncioSendWarning(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "socket.send() raised exception" not in msg

logging.getLogger("asyncio").addFilter(_SilenceAsyncioSendWarning())

# ── Constants ─────────────────────────────────────────────────────────────────

SAMPLE_RATE      = 16_000
FRAME_MS         = 80
FRAME_SAMPLES    = SAMPLE_RATE * FRAME_MS // 1000
BYTES_PER_SAMPLE = 2

# Silence detection
SILENCE_END_MS      = 1_400   # 1.4s pause ends an utterance
MIN_CAPTURE_MS      = 800     # ignore captures shorter than this
MAX_CAPTURE_SECONDS = 45

# ── Interrupt constants — calibrated to real human speech levels ─────────────
#
# Real-world RMS values measured on typical laptop mics:
#   Quiet room ambient noise:          RMS  150–400
#   Speaker echo at medium volume:     RMS  400–1,200
#   Normal conversational speech:      RMS  1,500–4,000   ← must trigger
#   Loud deliberate speech:            RMS  4,000–10,000  ← must trigger
#   Cough / door slam / single tap:    RMS  800–2,000 for <80ms  ← must NOT trigger
#
# Therefore:
#   - Threshold must be LOW enough that normal speech (RMS ~2,000) clears it
#   - Debounce must be LONG enough that a single cough (1 frame) does NOT clear it
#   - Baseline multiplier adapts to speaker echo WITHOUT raising the bar so high
#     that the user has to shout

# 5 frames × 80ms = 400ms of sustained speech — catches "Argus stop" clearly
INTERRUPT_FRAMES_REQUIRED = 5

# 1.5s grace at sentence start so the speaker transient can't self-trigger.
INTERRUPT_GRACE_SECS = 1.5

# Absolute RMS floor. Normal speech starts at ~1,500; we set floor slightly
# above typical speaker echo ceiling (~1,200) so echo still won't trigger.
INTERRUPT_ABS_FLOOR = 1_800

# Mic must be this many times the rolling ambient baseline to count.
# With echo baseline ~800: threshold = max(1800, 800 × 2.2) = 1,800. Good.
# With loud echo baseline ~1,400: threshold = max(1800, 1400 × 2.2) = 3,080. Good.
INTERRUPT_BASELINE_MULT = 2.2

# ── Voice selection — British male, deep, natural (Jarvis-style) ─────────────

# en-GB-RyanNeural is a deep, warm British male voice — closest to Jarvis.
# Fallback chain: Ryan → Thomas (UK) → Guy (US) → Davis (US) → local Supertonic.
EDGE_VOICE_PREFERENCES = [
    "en-GB-RyanNeural",           # deep warm British male — the Jarvis voice
    "en-GB-ThomasNeural",         # British male, slightly lighter
    "en-US-DavisNeural",          # US deep male fallback
    "en-US-GuyNeural",            # US warm male fallback
]
DEFAULT_VOICE = EDGE_VOICE_PREFERENCES[0]

# Supertonic fallback (local, no cloud call)
# SUPERTONIC_VOICE_FALLBACK removed — Supertonic was replaced by pyttsx3.

# ── Shared voice state (read by the chat toolbar) ────────────────────────────

class VoiceState:
    """Thread-safe shared state between the voice loop and the toolbar.

    The chat REPL imports this module and reads `current` on every
    toolbar refresh (every 50ms). The voice loop writes to it.
    Also broadcasts events to any connected HUD terminal windows.
    """
    __slots__ = ("status", "hint", "active")
    def __init__(self):
        self.status = "off"      # off|asleep|arming|listening|thinking|speaking
        self.hint   = ""
        self.active = False

    def set(self, status: str, hint: str = "") -> None:
        self.status = status
        self.hint   = hint
        self.active = (status != "off")
        # Broadcast to HUD windows (non-blocking, best-effort)
        _hud_send({"t": "state", "status": status, "hint": hint})

    def toolbar_text(self) -> str:
        """One-line string for the prompt_toolkit bottom toolbar."""
        if not self.active:
            return ""
        color_map = {
            "asleep":    ("\x1b[38;2;110;110;110m", "⟨◇⟩"),
            "arming":    ("\x1b[38;2;66;232;245m",  "⟨◈⟩"),
            "listening": ("\x1b[38;2;255;56;209m",  "⟨◈⟩"),
            "thinking":  ("\x1b[38;2;255;194;71m",  "⟨◆⟩"),
            "speaking":  ("\x1b[38;2;255;194;71m",  "⟨◆⟩"),
        }
        c, glyph = color_map.get(self.status, ("\x1b[0m", "⟨◇⟩"))
        rst = "\x1b[0m"
        dim = "\x1b[38;2;110;110;110m"
        label = self.status.upper().ljust(9)
        return f"  {c}{glyph}  {label}{rst}  {dim}{self.hint}{rst}"


# Module-level singleton so the toolbar can read it without importing the loop.
voice_state = VoiceState()

# ── Close phrases ────────────────────────────────────────────────────────────

# Full EXIT phrases — stop voice mode entirely
_CLOSE_PHRASES = (
    "end argus", "exit argus", "argus end", "argus exit",
    "argus shutdown", "argus shut down", "shutdown argus", "shut down argus",
    "goodbye argus", "argus goodbye", "argus stand down", "stand down argus",
)

# STOP-AND-CONTINUE phrases — interrupt current speech, then re-arm for next question.
# "Argus stop" kills the current TTS but keeps voice mode alive.
_STOP_SPEAKING_PHRASES = (
    "argus stop", "stop argus", "argus pause", "pause argus",
    "argus quiet", "quiet argus", "argus enough", "stop speaking",
    "stop talking", "argus wait",
)


def _is_close_phrase(transcript: str) -> bool:
    t = re.sub(r"[^a-z ]", "", (transcript or "").lower()).strip()
    return any(p in t for p in _CLOSE_PHRASES) if t else False


def _is_stop_speaking_phrase(transcript: str) -> bool:
    """True if the user wants ARGUS to stop speaking but NOT exit voice mode.
    After this, the listener immediately re-arms and waits for the next question.
    """
    t = re.sub(r"[^a-z ]", "", (transcript or "").lower()).strip()
    return any(p in t for p in _STOP_SPEAKING_PHRASES) if t else False

# ── Markdown cleaner — strip ALL formatting before TTS reads it ──────────────

def _clean_for_tts(text: str) -> str:
    """Remove every markdown construct so the TTS engine never reads
    asterisks, hashes, backticks, bullet dashes, or URLs aloud.

    Order matters — process multi-char patterns before single-char ones.
    """
    import re

    # Em-dashes / en-dashes → natural pause comma
    text = text.replace("—", ",").replace("–", ",").replace("…", "...")

    # ── HTML stripping (web search results can leak <tags> into the reply) ──
    # First, kill <script> and <style> blocks entirely (with their content).
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>",   "", text, flags=re.IGNORECASE)
    # Then strip any remaining HTML tags but keep their text content.
    text = re.sub(r"<[^>]+>", "", text)
    # Decode the most common entities so "&amp;" doesn't get read as "amp"
    for ent, ch in (("&nbsp;", " "), ("&amp;", "and"), ("&lt;", "<"),
                     ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        text = text.replace(ent, ch)

    # URL-like noise: drop bare http(s)://... and stylesheet-query strings
    # (e.g. "?family=Lato:400,700&display=swap") — never readable as speech.
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\?[a-zA-Z]+=\S+", "", text)

    # Fenced code blocks → replace with brief note, don't read the code
    text = re.sub(r"```[\s\S]*?```", "— code block omitted —", text)
    text = re.sub(r"`[^`\n]+`",      lambda m: m.group(0)[1:-1], text)

    # Headers: "## Title" → "Title"
    text = re.sub(r"^#{1,6}\s+",     "",  text, flags=re.MULTILINE)

    # Bold / italic: **text** / __text__ / *text* / _text_ → just text
    text = re.sub(r"\*\*([^*\n]+?)\*\*", r"\1", text)
    text = re.sub(r"__([^_\n]+?)__",     r"\1", text)
    text = re.sub(r"\*([^*\n]+?)\*",     r"\1", text)
    text = re.sub(r"_([^_\n]+?)_",       r"\1", text)

    # Links: [text](url) → just text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Inline images: ![alt](url) → nothing (images can't be spoken)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)

    # Blockquotes: "> text" → "text"
    text = re.sub(r"^>\s*", "", text, flags=re.MULTILINE)

    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)

    # Bullet lists: "- item" / "* item" / "+ item" → "item" (keep the text, lose the dash)
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)

    # Numbered lists: "1. item" → "item"
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)

    # Table pipes and alignment rows
    text = re.sub(r"\|[-: ]+\|[-| :]*", "", text)   # alignment row
    text = text.replace("|", " ")                      # remaining pipes

    # Collapse blank lines to single break, strip leading/trailing spaces per line
    lines = [ln.strip() for ln in text.splitlines()]
    # Remove consecutive blank lines
    out: list[str] = []
    prev_blank = False
    for ln in lines:
        blank = not ln
        if blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = blank
    text = "\n".join(out).strip()

    return text


# ── Sentence splitter for streaming TTS ──────────────────────────────────────

_SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"])|(?<=\n\n)")

def _split_sentences(text: str) -> list[str]:
    """Split text into speakable chunks without creating audible pauses.

    Rules that prevent the stuttering / long-pause problem:
      1. Never split after known abbreviations (Mr., Dr., Q4., etc.)
      2. Minimum chunk 120 chars — larger chunks = fewer gaps between files
      3. Only split at sentence-terminal punctuation followed by UPPERCASE,
         not at every period (avoids splitting "API v2. Check" correctly)
      4. Short replies (< 200 chars) returned as ONE chunk — zero gaps.
    """
    text = text.strip()
    if not text:
        return []
    # Short reply: speak as one piece, no inter-sentence gap at all
    if len(text) < 200:
        return [text]

    # Protect abbreviations from being treated as sentence ends
    protected = text
    _ABBREVS = [
        "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Inc.", "Ltd.", "Co.",
        "vs.", "etc.", "e.g.", "i.e.", "approx.", "fig.", "no.",
        "Jan.", "Feb.", "Mar.", "Apr.", "Jun.", "Jul.", "Aug.",
        "Sep.", "Oct.", "Nov.", "Dec.", "Q1.", "Q2.", "Q3.", "Q4.",
        "v1.", "v2.", "v3.", "API.", "URL.", "A.I.", "U.S.", "U.K.",
    ]
    _PLACEHOLDER = "\x00ABBREV\x00"
    _abbrev_map: dict[str, str] = {}
    for abbrev in _ABBREVS:
        ph = f"{_PLACEHOLDER}{len(_abbrev_map)}{_PLACEHOLDER}"
        _abbrev_map[ph] = abbrev
        protected = protected.replace(abbrev, ph)

    # Split only at sentence-terminal punctuation + capital letter
    boundary = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"])")
    raw = [s.strip() for s in boundary.split(protected) if s.strip()]

    # Restore abbreviations
    restored = []
    for chunk in raw:
        for ph, abbrev in _abbrev_map.items():
            chunk = chunk.replace(ph, abbrev)
        restored.append(chunk)

    # Merge chunks that are too short to avoid rapid-fire tiny audio files
    merged: list[str] = []
    for chunk in restored:
        if merged and len(merged[-1]) < 120:
            merged[-1] += " " + chunk
        else:
            merged.append(chunk)
    return merged or [text]

# ── TTS synthesis ────────────────────────────────────────────────────────────

async def _synth_edge(text: str, voice: str = DEFAULT_VOICE,
                       timeout: float = 10.0) -> Optional[Path]:
    """Synthesize via Edge TTS with a hard timeout.
    Returns .mp3 path or None on failure / timeout."""
    try:
        import edge_tts   # type: ignore
        import uuid, tempfile
        tmp = Path(tempfile.gettempdir()) / "argus_voice" / f"v_{uuid.uuid4().hex[:10]}.mp3"
        tmp.parent.mkdir(exist_ok=True)

        async def _do():
            await edge_tts.Communicate(text=text, voice=voice).save(str(tmp))

        await asyncio.wait_for(_do(), timeout=timeout)
        return tmp if tmp.exists() and tmp.stat().st_size > 100 else None
    except asyncio.TimeoutError:
        log.warning("edge-tts timed out after %.0fs (network issue) — using fallback", timeout)
        return None
    except Exception as e:
        log.debug("edge-tts %s failed: %s", voice, e)
        return None


async def _synth_macos_say(text: str) -> Optional[Path]:
    """macOS `say` command — instant, offline, no network needed.

    Produces an AIFF file which afplay can play natively. Used when
    edge-tts times out (network unreachable or Microsoft server down).
    Voice 'Daniel' is a natural British male voice built into macOS.
    Falls back to 'Alex' (US male) if Daniel isn't installed.
    """
    if not shutil.which("say"):
        return None
    import uuid, tempfile
    tmp = Path(tempfile.gettempdir()) / "argus_voice" / f"v_{uuid.uuid4().hex[:10]}.aiff"
    tmp.parent.mkdir(exist_ok=True)

    def _run():
        # Try Daniel (British male) first, then Alex (US male)
        for voice_name in ("Daniel", "Alex", "Tom"):
            r = subprocess.run(
                ["say", "-v", voice_name, "-o", str(tmp), text],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 100:
                return tmp
        # Last resort: default system voice, no -v flag
        subprocess.run(["say", "-o", str(tmp), text],
                        capture_output=True, timeout=15)
        return tmp if tmp.exists() and tmp.stat().st_size > 100 else None

    try:
        result = await asyncio.to_thread(_run)
        if result:
            log.info("macOS say TTS: %s (%d bytes)", result, result.stat().st_size)
        return result
    except Exception as e:
        log.debug("macos say failed: %s", e)
        return None


async def _synth_pyttsx3(text: str) -> Optional[Path]:
    """Cross-platform offline TTS via pyttsx3.

    On macOS this calls NSSpeechSynthesizer (same engine as `say`).
    On Linux it uses espeak. On Windows it uses SAPI5. This is the
    Linux/Windows fallback when neither Edge TTS nor `say` work.

    macOS users skip this — `_synth_macos_say` is already faster and
    uses higher-quality voices (Daniel, Alex).
    """
    if sys.platform == "darwin":
        return None   # macOS say is strictly better
    try:
        import pyttsx3   # type: ignore
        import uuid, tempfile
        tmp = Path(tempfile.gettempdir()) / "argus_voice" / f"v_{uuid.uuid4().hex[:10]}.wav"
        tmp.parent.mkdir(exist_ok=True)

        def _do():
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.save_to_file(text, str(tmp))
            engine.runAndWait()

        await asyncio.to_thread(_do)
        return tmp if tmp.exists() and tmp.stat().st_size > 100 else None
    except ImportError:
        log.debug("pyttsx3 not installed")
        return None
    except Exception as e:
        log.debug("pyttsx3 failed: %s", e)
        return None


async def synth_best(text: str) -> Optional[Path]:
    """Synthesize audio using the best available backend.

    Priority (each layer is a graceful fallback for the previous):
      1. Edge TTS — Microsoft Edge cloud voice (British Ryan Neural,
         deep male). Best quality. Needs network. 8s timeout per voice.
      2. macOS `say` — Apple's built-in Daniel/Alex voices. Instant.
         Offline. Excellent quality on Mac.
      3. pyttsx3 — Cross-platform offline TTS (Linux espeak / Windows SAPI).
         Lower quality but ensures voice ALWAYS works.

    Returns None only if ALL three backends fail. The voice listener
    treats that as a "text-only reply" and continues.
    """
    # Layer 1: Edge TTS with short timeout so a flaky network doesn't hang us
    for voice in EDGE_VOICE_PREFERENCES[:2]:
        path = await _synth_edge(text, voice, timeout=8.0)
        if path:
            return path

    # Layer 2: macOS `say` (best offline option on Mac)
    path = await _synth_macos_say(text)
    if path:
        return path

    # Layer 3: pyttsx3 (cross-platform offline; no-op on macOS)
    return await _synth_pyttsx3(text)

# ── Player ───────────────────────────────────────────────────────────────────

def _player_cmd() -> Optional[list[str]]:
    return (
        ["afplay"]                                                   if shutil.which("afplay")  else
        ["aplay", "-q"]                                              if shutil.which("aplay")   else
        ["paplay"]                                                   if shutil.which("paplay")  else
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]     if shutil.which("ffplay")  else
        None
    )

class _Player:
    def __init__(self, path: Path):
        cmd = _player_cmd()
        self._proc = (subprocess.Popen(
            cmd + [str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) if cmd else None)

    def running(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=0.5)
            except Exception:
                try: self._proc.kill()
                except Exception: pass

# ── VAD ──────────────────────────────────────────────────────────────────────

def _load_vad() -> Any:
    try:
        import webrtcvad
        return webrtcvad.Vad(2)
    except ImportError:
        return None

def _is_speech(pcm: bytes, vad: Any) -> bool:
    if vad:
        sl = SAMPLE_RATE * 20 // 1000 * BYTES_PER_SAMPLE
        try:
            for i in range(0, len(pcm) - sl + 1, sl):
                if vad.is_speech(pcm[i:i + sl], SAMPLE_RATE):
                    return True
            return False
        except Exception:
            pass
    import numpy as np
    arr = np.frombuffer(pcm, dtype=np.int16).astype("float32")
    return float(np.sqrt(np.mean(arr * arr))) > 800.0 if arr.size > 0 else False

# ── Streaming TTS speaker ─────────────────────────────────────────────────────

def _rms(pcm: bytes) -> float:
    """Root-mean-square of a 16-bit PCM buffer — the true mic level."""
    import numpy as np
    arr = np.frombuffer(pcm, dtype=np.int16).astype("float32")
    return float(np.sqrt(np.mean(arr * arr))) if arr.size > 0 else 0.0


async def speak_streaming(text: str, vad: Any, stop_flag: dict) -> bool:
    """Fluid TTS with echo-safe interrupt detection.

    Echo handling — the speaker output feeds back into the mic during playback.
    We use a TWO-PHASE listener:
      • During TTS playback: VERY strict threshold (RMS > 4500 sustained 300ms)
        so normal speaker echo (~1500-3000 RMS) is ignored — only loud human
        speech close to the mic interrupts.
      • In the ~150ms pause between sentences: standard threshold — easier to
        catch a normal "Argus stop" said during the natural breath.

    Shutdown — stop_flag["stop"] is checked everywhere:
      • Before synth (producer skips)
      • Before play (consumer skips)
      • Inside the monitor loop (kills the current player)
      • Inside the player wait (timeout check every 200ms)
    Closing the HUD window flips stop_flag["stop"] → everything halts cleanly.
    """
    from collections import deque
    import threading as _thr

    text = _clean_for_tts(text)
    if not text.strip():
        return False

    sentences   = _split_sentences(text)
    synth_q     : asyncio.Queue[Optional[Path]] = asyncio.Queue()
    interrupted = {"v": False}
    player_lock = _thr.Lock()
    cur_player  : dict = {"p": None}
    speaking_now = {"v": False}   # kept for backward compat with kill paths

    # NOTE: the mic-based interrupt monitor was REMOVED. It caused the
    # "ARGUS cuts off after one sentence" bug — the speaker output bled into
    # the mic and tripped the monitor regardless of threshold. The user can:
    #   • Close the HUD popup     → instant stop (stop_flag is honored)
    #   • Say "End Argus"        → caught by the wake-word loop after this returns
    #   • Press Ctrl-C            → caught by the main process
    # That's enough control. If you want word-by-word barge-in back, build
    # acoustic echo cancellation first (CoreAudio's voice-processing audio
    # unit on macOS) — anything less is unreliable.

    # ── Producer: synthesize sentences ahead of playback ────────────────────
    async def _producer():
        for sent in sentences:
            if stop_flag.get("stop") or interrupted["v"]:
                break
            path = await synth_best(sent)
            if stop_flag.get("stop") or interrupted["v"]:
                break
            await synth_q.put(path)
        await synth_q.put(None)

    # ── Consumer: play each sentence sequentially ───────────────────────────
    async def _consumer():
        loop = asyncio.get_event_loop()
        while not stop_flag.get("stop"):
            try:
                path = await asyncio.wait_for(synth_q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if stop_flag.get("stop"):
                    break
                continue
            if path is None or interrupted["v"] or stop_flag.get("stop"):
                break
            if not path or not path.exists():
                continue

            player = _Player(path)
            with player_lock:
                cur_player["p"] = player
            _active_player_ref["p"] = player   # module-level for HUD-close killer

            voice_state.set("speaking", "ARGUS talking — say 'Argus stop' to interrupt")
            speaking_now["v"] = True

            # Block until this sentence finishes, the monitor kills it,
            # or stop_flag is raised. Check stop_flag every 200ms.
            def _wait_with_stop_check():
                if not player._proc:
                    return
                while True:
                    try:
                        rc = player._proc.wait(timeout=0.2)
                        return rc
                    except subprocess.TimeoutExpired:
                        if stop_flag.get("stop") or interrupted["v"]:
                            try: player._proc.kill()
                            except Exception: pass
                            return -1
                    except Exception:
                        return -1

            await loop.run_in_executor(None, _wait_with_stop_check)

            # Brief inter-sentence gap with relaxed listening (mic catches
            # "Argus stop" said during the natural breath between sentences)
            speaking_now["v"] = False
            with player_lock:
                cur_player["p"] = None
            _active_player_ref["p"] = None

            if stop_flag.get("stop") or interrupted["v"]:
                # Drain any queued synthesised audio so nothing else plays
                while not synth_q.empty():
                    try: synth_q.get_nowait()
                    except Exception: pass
                break

    try:
        await asyncio.gather(_producer(), _consumer())
    finally:
        # Always clean up: kill any in-flight player, signal monitor to stop
        speaking_now["v"] = False
        interrupted["v"]  = True
        with player_lock:
            p = cur_player.get("p")
        if p:
            try: p.kill()
            except Exception: pass

    return interrupted.get("v", False) or stop_flag.get("stop", False)

# ── STT ───────────────────────────────────────────────────────────────────────

async def transcribe(audio: bytes, model_size: str = "tiny.en") -> Optional[str]:
    """Transcribe raw PCM bytes. Returns stripped text or None."""
    from argus.voice import stt as _stt
    try:
        r = await asyncio.wait_for(
            _stt.transcribe(audio, model_size=model_size),
            timeout=30.0,
        )
        return r.text.strip() if r and r.text.strip() else None
    except asyncio.TimeoutError:
        log.warning("transcribe timed out")
        return None

# ── Capture loop ──────────────────────────────────────────────────────────────

async def _capture_loop(*, wake_detector: Any, vad: Any, stop_flag: dict,
                         clap_det: Any = None, clap_flag: dict) -> bytes:
    """Mic -> wake gate -> capture until silence. Returns raw PCM bytes.

    If the mic / sounddevice / driver fails, return b"" and let the outer
    loop sleep before retrying — prevents tight error spam.
    """
    try:
        import sounddevice as sd   # type: ignore
    except ImportError:
        _print("error", "sounddevice not installed. Run: uv sync --extra voice-wake")
        stop_flag["mic_broken"] = True   # signal outer loop to back off
        await asyncio.sleep(5.0)          # don't spam this error
        return b""

    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
    loop = asyncio.get_event_loop()

    def _cb(indata, frames, _t, _s):
        import numpy as np
        pcm = (indata[:, 0] * 32767.0).clip(-32768, 32767).astype("int16").tobytes()
        try: loop.call_soon_threadsafe(q.put_nowait, pcm)
        except Exception: pass

    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                 dtype="float32", blocksize=FRAME_SAMPLES,
                                 callback=_cb)
        stream.start()
    except Exception as e:
        _print("error", f"microphone open failed: {e}")
        return b""

    captured     = bytearray()
    silence_ms   = 0
    capture_t0: Optional[float] = None
    armed        = (wake_detector is None)
    arming_until = 0.0

    try:
        while not stop_flag["stop"]:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # ── ASLEEP: wait for wake word or clap
            if not armed:
                wake_hit = wake_detector and wake_detector.feed(chunk)
                if clap_det:
                    clap_det.feed(chunk)
                clap_hit = bool(clap_flag.get("hit"))
                if wake_hit or clap_hit:
                    clap_flag["hit"] = False
                    armed        = True
                    arming_until = time.monotonic() + 0.25
                    voice_state.set("arming", "hey Argus heard - arming" if wake_hit else "clap detected")
                continue

            # ── ARMING: 250ms grace period
            if time.monotonic() < arming_until:
                continue

            # ── CAPTURE
            if capture_t0 is None:
                capture_t0 = time.monotonic()
                voice_state.set("listening", "recording - speak now")

            captured.extend(chunk)
            speech = _is_speech(chunk, vad)

            if speech:
                silence_ms = 0
            else:
                silence_ms += FRAME_MS

            elapsed_ms = int((time.monotonic() - capture_t0) * 1000)

            if silence_ms >= SILENCE_END_MS:
                if elapsed_ms >= MIN_CAPTURE_MS:
                    break   # clean utterance end
                # Too short — reset
                captured.clear()
                silence_ms = 0
                capture_t0 = None
                if wake_detector:
                    armed = False
                    if hasattr(wake_detector, "reset"):
                        wake_detector.reset()
                    voice_state.set("asleep", "too short - waiting for 'Hey Argus'")
                else:
                    voice_state.set("listening", "too short - speak again")

            if elapsed_ms > MAX_CAPTURE_SECONDS * 1000:
                break
    finally:
        try: stream.stop(); stream.close()
        except Exception: pass
        if wake_detector and hasattr(wake_detector, "reset"):
            wake_detector.reset()

    return bytes(captured)

# ── Agent turn ────────────────────────────────────────────────────────────────

async def _agent_turn_streaming(user_text: str, on_sentence: Callable[[str], None]) -> str:
    """Drive the agent loop with all tools loaded, calling on_sentence() at
    each sentence boundary so TTS can start before the reply is complete.

    Returns the complete reply text.

    Web search and all other tools must be registered before the agent loop
    runs. The voice process may not have called register_defaults() yet, so
    we call it here explicitly — it's idempotent (safe to call multiple times).
    """
    from argus import config as _config
    from argus.loop import run_turn, Conversation
    from argus.tools.registry import register_defaults
    register_defaults()   # ensure all tools (web_search, scribe, ledger, etc.) are live

    cfg   = _config.load()
    convo = Conversation()

    # Prepend the ARGUS voice persona instruction so the response sounds like
    # ARGUS — British, precise, confident, witty — and arrives in natural spoken
    # English (no markdown, bullet points, or symbols).
    _VOICE_PREFIX = (
        "[VOICE MODE]\n"
        "You are ARGUS, the hundred-eyed sentinel, speaking ALOUD via text-to-speech.\n"
        "- Sound like a refined British intelligence — calm, direct, precise, occasionally dry.\n"
        "- Use natural spoken English only. NO asterisks, dashes, bullet points, or symbols.\n"
        "- Keep responses concise and conversational. 1-3 sentences for simple queries.\n"
        "- For research or multi-step tasks: announce what you're doing, then do it.\n"
        "- You have full access to all ARGUS tools: web search, file creation, Gmail, etc.\n"
        "  Use them without hesitation when the user's question requires real data.\n"
        "- Sentence example: 'Let me check that now.' then call the tool.\n\n"
        "User said (spoken): "
    )
    user_text = _VOICE_PREFIX + user_text
    full: list[str] = []
    tool_event_count = 0

    try:
        async for evt in run_turn(cfg, convo, user_text):
            etype = type(evt).__name__
            if etype == "TextEvent" and getattr(evt, "text", None):
                full.append(evt.text)
            elif etype == "ToolCallEvent":
                tool_event_count += 1
                log.info("voice tool call: %s", getattr(evt, "name", "?"))
    except Exception as e:
        log.exception("voice agent turn failed")
        err_msg = f"I hit an error while processing that. {type(e).__name__}: {e}"
        on_sentence(err_msg)
        return err_msg

    reply = "".join(full).strip()
    if not reply:
        # Some providers stream nothing if the response was entirely tool calls
        # that resulted in errors. Surface that clearly.
        reply = ("I tried but received no usable answer."
                 if tool_event_count == 0
                 else f"I called {tool_event_count} tool(s) but got no usable answer.")

    # Hand the FULL reply to the TTS layer in one shot. This eliminates the
    # cut-off-after-first-sentence bug caused by speaker echo tripping the
    # interrupt monitor between streaming chunks. Sentence splitting inside
    # speak_streaming() handles natural pause boundaries.
    on_sentence(reply)
    return reply

# ── Voice loop (runs as background task in the chat REPL) ────────────────────

async def run_voice_background(*, wake_model: str = "hey_argus",
                                 stt_model: str = "tiny.en",
                                 speak: bool = True,
                                 stop_event=None) -> None:
    """Main voice loop. Runs inside its own asyncio event loop in a daemon
    thread so the chat REPL prompt stays live. `stop_event` can be a
    threading.Event (for cross-thread signalling) or an asyncio.Event.
    """
    import threading as _threading
    stop_flag = {"stop": False}

    def _poll_stop():
        """Background thread that polls the stop_event and flips stop_flag."""
        if stop_event is None:
            return
        try:
            # threading.Event uses .wait(); asyncio.Event is async
            if isinstance(stop_event, _threading.Event):
                stop_event.wait()        # blocks until set()
            # If it's some other object with a set attribute we skip it
        except Exception:
            pass
        stop_flag["stop"] = True

    if stop_event:
        t = _threading.Thread(target=_poll_stop, daemon=True)
        t.start()

    # VAD
    vad = _load_vad()

    # Wake word
    detector = None
    if True:  # always try
        try:
            from argus.voice.wakeword import Detector
            d = Detector(wake_model)
            detector = d if d.is_available() else None
            wake_enabled = detector is not None
            if not wake_enabled:
                voice_state.set("asleep", "VAD-only (openwakeword unavailable)")
        except Exception:
            wake_enabled = False
    else:
        wake_enabled = False

    # Clap
    clap_flag = {"hit": False}
    clap_det  = None
    if wake_enabled:
        try:
            from argus.voice.clap import ClapDetector
            clap_det = ClapDetector(
                on_double=lambda: clap_flag.__setitem__("hit", True),
                on_triple=lambda: clap_flag.__setitem__("hit", True),
            )
        except Exception:
            pass

    # Start HUD socket server (non-fatal — HUD window is optional).
    # Register the stop callback so closing the popup kills the voice loop.
    def _on_hud_close():
        """Called when the last HUD window disconnects (user closed popup).

        Three things must happen IMMEDIATELY (not on the next loop iteration):
          1. Flip the stop flag → producer/consumer/monitor all see it
          2. Kill any TTS audio that's currently playing (the speaker keeps
             going otherwise — the subprocess holds the audio device).
          3. Mark voice_state as 'off' so the HUD never shows a stale label.
        """
        stop_flag["stop"] = True
        _kill_active_player()             # speaker silent NOW
        try:
            voice_state.set("off", "popup closed — voice stopped")
        except Exception: pass
        log.info("Voice loop stopping because HUD window was closed")

    register_hud_close_callback(_on_hud_close)
    hud_server = await _start_socket_server()

    # Pre-warm STT
    voice_state.set("asleep", "warming up speech engine...")
    from argus.voice import stt as _stt
    await _stt.prewarm(stt_model)

    wake_detail = detector.describe() if detector else "VAD-only"
    _print("info", f"Voice ready | wake: {wake_detail} | stt: {stt_model} | voice: {DEFAULT_VOICE}")
    _print("info", "Say 'Hey Argus' to wake. Say 'End Argus' to close.")

    # Startup greeting — ARGUS introduces himself so the user knows it's live
    voice_state.set("speaking", "greeting…")
    greeting = "Hi, this is Argus. How can I help you?"
    _hud_send({"t": "argus", "text": greeting})
    try:
        path = await synth_best(greeting)
        if path:
            import subprocess as _sp, shutil as _sh
            player_cmd = (["afplay"] if _sh.which("afplay") else
                          ["aplay", "-q"] if _sh.which("aplay") else
                          ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
                          if _sh.which("ffplay") else None)
            if player_cmd:
                await asyncio.to_thread(
                    lambda: _sp.run(player_cmd + [str(path)],
                                     capture_output=True, timeout=15)
                )
    except Exception as e:
        log.debug("greeting synthesis failed: %s", e)

    voice_state.set("asleep",
                     "waiting for 'Hey Argus'" if wake_enabled else "speak any time")

    # TTS sentence queue for streaming
    tts_q: asyncio.Queue[Optional[str]] = asyncio.Queue()

    async def _tts_worker():
        """Dequeue synthesized sentences and play them as they arrive."""
        while not stop_flag["stop"]:
            sentence = await tts_q.get()
            if sentence is None:
                break
            if speak and not stop_flag["stop"]:
                voice_state.set("speaking", "speaking - talk to interrupt")
                interrupted = await speak_streaming(sentence, vad, stop_flag)
                if interrupted:
                    # Clear remaining queued sentences so we don't play stale audio
                    while not tts_q.empty():
                        try: tts_q.get_nowait()
                        except Exception: pass

    tts_worker_task = asyncio.create_task(_tts_worker())

    try:
        while not stop_flag["stop"]:
            audio = await _capture_loop(
                wake_detector=detector, vad=vad,
                stop_flag=stop_flag,
                clap_det=clap_det, clap_flag=clap_flag,
            )
            if stop_flag["stop"]:
                break
            if not audio:
                voice_state.set("asleep",
                                 "waiting for 'Hey Argus'" if wake_enabled else "speak any time")
                continue

            voice_state.set("thinking", "transcribing...")
            text = await transcribe(audio, stt_model)
            if not text:
                voice_state.set("asleep", "no speech detected")
                continue

            _print("user", text)
            _hud_send({"t": "user", "text": text})

            # "End Argus" → exit voice mode entirely
            if _is_close_phrase(text):
                _print("info", "Argus standing down. Voice mode closed.")
                break

            # "Argus stop" → kill current speech, re-arm immediately for next question
            if _is_stop_speaking_phrase(text):
                _print("info", "Stopped. Ready for your next question.")
                # Drain any queued TTS (the tts_worker will stop its current segment
                # naturally since we're not sending new ones right now)
                voice_state.set("asleep",
                                 "say 'Hey Argus' to continue" if True else "speak now")
                continue

            voice_state.set("thinking", "thinking...")

            # Feed sentences to TTS as they stream from the LLM
            full_reply_parts: list[str] = []
            def _on_sentence(sent: str) -> None:
                full_reply_parts.append(sent)
                if speak:
                    tts_q.put_nowait(sent)

            reply = await _agent_turn_streaming(text, _on_sentence)
            # Strip any HTML / markdown / URL-noise before showing in HUD too —
            # web search tools sometimes inject raw snippets into the response.
            clean_reply = _clean_for_tts(reply) if reply else ""
            _print("argus", clean_reply)
            _hud_send({"t": "argus", "text": clean_reply[:500]})   # cap HUD entry size

            voice_state.set("asleep",
                             "say 'Hey Argus' to continue" if wake_enabled else "speak to continue")
    finally:
        # Signal TTS worker to stop
        await tts_q.put(None)
        try: await asyncio.wait_for(tts_worker_task, timeout=5.0)
        except Exception: tts_worker_task.cancel()
        voice_state.set("off", "")


# ── Standalone entry (argus listen CLI command) ───────────────────────────────

def run_listener(*, wake: bool = True,
                  wake_model: str = "hey_argus",
                  stt_model: str = "tiny.en",
                  tts_provider: Optional[str] = None,
                  speak_replies: bool = True) -> None:
    """Standalone voice listener — same CLI stays visible, prompt usable.
    Blocks until 'End Argus' or Ctrl-C. Used by `argus listen` CLI command.
    """
    import threading
    stop_event = threading.Event()

    def _on_sigint(*_):
        stop_event.set()
    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (ValueError, AttributeError):
        pass

    try:
        asyncio.run(run_voice_background(
            wake_model=wake_model,
            stt_model=stt_model,
            speak=speak_replies,
            stop_event=stop_event,
        ))
    except KeyboardInterrupt:
        pass
    finally:
        voice_state.set("off", "")
        sys.stdout.write("\n")
        sys.stdout.flush()


def start_voice_thread(*, wake_model: str = "hey_argus",
                        stt_model: str = "tiny.en",
                        speak: bool = True,
                        open_hud: bool = True) -> "threading.Event":
    """Launch voice in a daemon thread so the caller stays unblocked.

    If `open_hud=True` (default), opens a second Terminal window showing
    the ARGUS voice HUD (brand-colored waveform + live transcription).
    Closing that window stops voice mode automatically via the socket.

    Returns a threading.Event you can call .set() on to stop the voice loop.
    """
    import threading
    stop_event = threading.Event()

    def _run():
        try:
            asyncio.run(run_voice_background(
                wake_model=wake_model,
                stt_model=stt_model,
                speak=speak,
                stop_event=stop_event,
            ))
        except Exception as e:
            import traceback
            err = traceback.format_exc()
            log.error("voice thread crashed: %s", err)
            # Write to log file so the user can see it
            try:
                from argus import paths
                log_path = paths.LOGS_DIR / "voice.log"
                paths.LOGS_DIR.mkdir(exist_ok=True)
                with log_path.open("a") as fh:
                    import time as _t
                    fh.write(f"\n--- {_t.strftime('%Y-%m-%d %H:%M:%S')} ---\n{err}\n")
                sys.stderr.write(f"\n[ARGUS voice error] {type(e).__name__}: {e}\n"
                                  f"  Full log: {log_path}\n")
                sys.stderr.flush()
            except Exception:
                sys.stderr.write(f"\n[ARGUS voice error] {type(e).__name__}: {e}\n")
                sys.stderr.flush()
        finally:
            voice_state.set("off", "")

    t = threading.Thread(target=_run, daemon=True, name="argus-voice")
    t.start()

    if open_hud:
        def _open_later():
            import time as _t
            _t.sleep(1.2)   # give socket server time to bind
            _spawn_hud_window()
        threading.Thread(target=_open_later, daemon=True, name="argus-hud-spawn").start()

    return stop_event


def _find_argus_binary() -> str:
    """Locate the argus binary robustly.

    Search order:
      1. Same directory as sys.executable (i.e. the active venv's bin/)
      2. ~/.local/bin/argus  (uv installs here)
      3. PATH lookup
    """
    # Option 1: venv bin/ next to the running Python
    venv_bin = Path(sys.executable).parent / "argus"
    if venv_bin.exists():
        return str(venv_bin)
    # Option 2: uv default location
    uv_bin = Path.home() / ".local" / "bin" / "argus"
    if uv_bin.exists():
        return str(uv_bin)
    # Option 3: PATH
    found = shutil.which("argus")
    if found:
        return found
    # Fallback: python -m argus.cli
    return f"{sys.executable} -m argus.cli"


def _spawn_hud_window() -> None:
    """Open a new Terminal.app window running `argus voice-hud`.

    Uses a heredoc AppleScript passed via stdin — avoids ALL quote-escaping
    issues with paths that contain spaces or special characters.
    """
    argus_bin = _find_argus_binary()
    log.info("HUD spawn: binary=%s", argus_bin)

    if not shutil.which("osascript"):
        log.info("osascript not found — run `argus voice-hud` in a second terminal")
        return

    # Write the AppleScript to a temp file to avoid shell-escaping entirely.
    # We do NOT use `with profile` — it fails if the profile doesn't exist.
    # Instead we set window properties after opening.
    import tempfile, textwrap
    # Compact two-column popup — landscape format that fits animation+transcript.
    # At 11pt Menlo (~7px/char, ~14px/row):
    #   width  840 - 40 = 800px  →  ~108 cols (animation 48 + transcript 55)
    #   height 460 - 44 = 416px  →  ~27 rows
    # Smallest size that still renders a readable two-column layout.
    script = textwrap.dedent(f"""\
        set argus_bin to "{argus_bin}"
        set cmd to argus_bin & " voice-hud"
        tell application "Terminal"
            activate
            do script cmd
            delay 0.4
            tell front window
                set custom title to "ARGUS Voice"
                set bounds to {{40, 44, 840, 460}}
                tell selected tab
                    try
                        set font size to 11
                    end try
                end tell
            end tell
        end tell
    """)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".scpt", delete=False, prefix="argus_hud_"
        ) as tf:
            tf.write(script)
            tf_path = tf.name

        result = subprocess.run(
            ["osascript", tf_path],
            capture_output=True, text=True, timeout=6,
        )
        os.unlink(tf_path)

        if result.returncode != 0:
            log.warning("HUD window failed: %s", result.stderr[:300])
        else:
            log.info("HUD window opened OK")
    except Exception as e:
        log.warning("HUD window spawn exception: %s", e)

# ── Helpers for console output (below the fixed HUD) ─────────────────────────

def _print(role: str, text: str) -> None:
    """Print a voice transcript line to stderr (bypasses prompt_toolkit).

    KEY BEHAVIOUR: when a HUD window is connected, user/argus transcript
    lines are suppressed here — they're already shown in the popup.
    Only errors and info messages still appear in the main CLI.

    This keeps the main CLI clean while the popup shows the conversation.
    """
    # Transcript roles: show ONLY in HUD when one is connected
    if role in ("user", "argus") and _hud_writers:
        return   # HUD handles it — don't duplicate in main CLI

    CYAN    = "\x1b[38;2;66;232;245m"
    GOLD    = "\x1b[38;2;255;194;71m"
    DIM     = "\x1b[38;2;110;110;110m"
    BEIGE   = "\x1b[38;2;245;230;200m"
    RST     = "\x1b[0m"
    RED     = "\x1b[38;2;255;92;92m"

    if role == "user":
        line = f"\n  {CYAN}you{RST}  {DIM}›{RST}  {BEIGE}{text}{RST}"
    elif role == "argus":
        try:
            from argus.render import clean_text
            text = clean_text(text)
        except Exception:
            pass
        line = f"\n  {GOLD}argus{RST}  {DIM}▸{RST}  {BEIGE}{text}{RST}"
    elif role == "info":
        line = f"  {DIM}⟨◇⟩  {text}{RST}"
    elif role == "error":
        line = f"\n  {RED}✗{RST}  {text}"
    else:
        line = f"  {text}"

    sys.stderr.write(line + "\n")
    sys.stderr.flush()

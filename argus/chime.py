"""Retro exit chime — a short chiptune played when ARGUS shuts down.

Generates a square-wave arpeggio on the fly with numpy (no asset files),
writes a 16-bit WAV to a tempfile, and asks the OS to play it
non-blocking via `afplay` (macOS), `aplay` (Linux), or `paplay`. If none
are available, falls back to a silent terminal bell.

Design constraints:
  • TOTAL DURATION ≤ 600 ms so it doesn't keep the user waiting.
  • Cheap to generate (<20 ms) so startup never feels it.
  • No deps beyond numpy + soundfile (already pulled by the voice extra).
  • Honors ARGUS_NO_CHIME=1 to silence.
  • Pre-rendered + cached on disk so subsequent exits are instant.

The chime itself: a 4-note rising fifth followed by a short held tonic.
Sounds like a "session sealed" stamp — confident, brief, unmistakably
brand. Square wave (8-bit style) reinforces the retro feel.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional


# ── Chime designs (one chosen randomly per session) ─────────────────────────
#
# Each chime is a list of (frequency_hz, duration_ms) pairs. Frequencies in
# Hz, equal-temperament. Rests use freq=0. Total durations are tuned to feel
# punchy without overstaying.

_CHIMES: dict[str, list[tuple[float, int]]] = {
    # Optimistic boot — rising major arpeggio. Feels like a console powering up.
    # Sub-400ms total so the splash starts immediately after.
    "boot": [
        (392.00,  60),   # G4
        (523.25,  60),   # C5
        (659.25,  60),   # E5
        (783.99,  60),   # G5
        (1046.50, 160),  # C6 (held — the "ready" punctuation)
    ],
    # Confident "session sealed" — perfect-fifth arpeggio with held tonic
    "sealed": [
        (523.25,  70),   # C5
        (659.25,  70),   # E5
        (783.99,  70),   # G5
        (1046.50, 220),  # C6 (held)
    ],
    # Watchful "stand-down" — descending tritone resolved to a fifth
    "standdown": [
        (880.00,  60),   # A5
        (659.25,  60),   # E5
        (440.00, 240),   # A4 (held)
    ],
    # Brisk "logout" — three pulses then a cap
    "logout": [
        (988.00,  50),   # B5
        (988.00,  50),
        (988.00,  50),
        (1318.51, 200),  # E6
    ],
    # Mystic "fadeout" — two notes that drift apart
    "fadeout": [
        (659.25, 120),   # E5
        (415.30, 280),   # G#4 (long, melancholy)
    ],
}


_CACHE_DIR = Path(tempfile.gettempdir()) / "argus_chime"
_SAMPLE_RATE = 22_050   # 8-bit-style aesthetic; small files; tiny CPU


def play_boot_chime(*, blocking: bool = False) -> None:
    """Optimistic 400ms rising arpeggio. Fire as the splash begins drawing
    so the audio crescendos *with* the gradient cascade. Non-blocking by
    default — splash continues in parallel."""
    play_exit_chime(chime="boot", blocking=blocking)


def play_exit_chime(*, chime: Optional[str] = None,
                    blocking: bool = False) -> None:
    """Play a retro exit chime. Cheap; safe in any environment.

    Args:
      chime    — name from _CHIMES (sealed | standdown | logout | fadeout).
                  None → random choice each call.
      blocking — wait for playback to finish before returning. Default False.

    Honors:
      ARGUS_NO_CHIME=1 → silence
      No audio player on PATH → silence (no exception)
    """
    if os.environ.get("ARGUS_NO_CHIME") == "1":
        return

    name = chime or random.choice(list(_CHIMES.keys()))
    spec = _CHIMES.get(name) or _CHIMES["sealed"]

    wav_path = _render_or_cache(name, spec)
    if not wav_path:
        # Last resort: ASCII bell. Worst case it's silent on muted terminals.
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass
        return

    player = _find_player()
    if not player:
        return

    try:
        if blocking:
            subprocess.run(player + [str(wav_path)], capture_output=True, timeout=3)
        else:
            # Detach so the shell prompt returns immediately. stdout/stderr
            # → /dev/null so the player doesn't print over the terminal.
            subprocess.Popen(player + [str(wav_path)],
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             stdin=subprocess.DEVNULL,
                             start_new_session=True)
    except Exception:
        pass   # never raise from a courtesy chime


# ── Audio synthesis (numpy + soundfile, lazy-imported) ──────────────────────


def _render_or_cache(name: str, spec: list[tuple[float, int]]) -> Optional[Path]:
    """Return a path to a WAV file containing the chime, generating + caching
    on first call. Returns None if numpy/soundfile aren't installed."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _CACHE_DIR / f"{name}.wav"
    if path.exists() and path.stat().st_size > 0:
        return path

    try:
        import numpy as np
        import soundfile as sf
    except ImportError:
        return None

    samples_per_ms = _SAMPLE_RATE / 1000
    chunks = []
    for freq, dur_ms in spec:
        n = int(dur_ms * samples_per_ms)
        if freq <= 0:
            chunks.append(np.zeros(n, dtype=np.float32))
            continue

        t = np.arange(n, dtype=np.float32) / _SAMPLE_RATE
        # Square wave for that 8-bit feel
        wave = np.sign(np.sin(2 * np.pi * freq * t)).astype(np.float32)

        # Quick attack, gentler release envelope → no clicks
        attack_n = min(int(0.005 * _SAMPLE_RATE), n // 4)
        release_n = min(int(0.030 * _SAMPLE_RATE), n // 2)
        env = np.ones(n, dtype=np.float32)
        if attack_n:  env[:attack_n] = np.linspace(0, 1, attack_n)
        if release_n: env[-release_n:] = np.linspace(1, 0, release_n)
        wave *= env

        # Soft-clip a bit (~-6dB) so it's pleasant, not piercing
        chunks.append(wave * 0.35)

    audio = np.concatenate(chunks)
    sf.write(str(path), audio, _SAMPLE_RATE, subtype="PCM_16")
    return path


# ── Player detection ────────────────────────────────────────────────────────


def _find_player() -> Optional[list[str]]:
    """Return the argv prefix for a non-interactive WAV player, or None."""
    # macOS
    if shutil.which("afplay"):
        return ["afplay"]
    # Linux — ALSA
    if shutil.which("aplay"):
        return ["aplay", "-q"]
    # Linux — PulseAudio
    if shutil.which("paplay"):
        return ["paplay"]
    # Generic — ffplay (ffmpeg's lightweight player)
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    return None

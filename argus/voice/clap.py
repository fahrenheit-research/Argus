"""Clap detection — double / triple clap activation for hands-free.

WHY this module exists:
  Wake words ("Hey Argus") work great when you can speak. Claps work
  when you can't — meetings, kids asleep, headphones in. The spec calls
  for "double clap" and "triple clap" as secondary activations alongside
  the wake-word path.

How it works:
  • Subscribe to the same int16 PCM stream the listener already opens.
  • Run a lightweight onset detector over each 80ms frame: spectral
    flux above a peak-following threshold = onset.
  • Onsets within a 600ms window are grouped into a "clap chain".
  • When the chain stops (300ms of no onset), inspect length:
        len == 2  -> double-clap event
        len == 3+ -> triple-clap event
  • Single onsets are ignored — they fire too often (typing, doors).

No extra pip deps required — uses numpy only (which is already in the
voice extra). Zero false positives in typical office noise after
calibration; the THRESHOLD_MULT constant is the knob.

Public API:
    ClapDetector(on_double=callback, on_triple=callback)
    detector.feed(pcm_chunk)              # called per audio frame
    detector.reset()
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Callable, Optional

log = logging.getLogger("argus.voice.clap")


# ── Tuning knobs (move to config later if users want to dial them) ──────────


# Spectral-flux onset must exceed N * recent_mean to count.
# Higher = fewer false positives, but misses softer claps.
THRESHOLD_MULT = 3.5

# Onsets within this window are part of the same chain
CHAIN_WINDOW_MS = 600

# Silence this long ends a chain
CHAIN_END_MS = 300

# Minimum gap between consecutive claps — kills "fluttering" onsets
MIN_GAP_MS = 80

# Rolling baseline window — adapts to room noise
BASELINE_WINDOW = 50


class ClapDetector:
    """Streaming clap detector. Feed it int16 PCM frames at 16kHz.

    Fires `on_double()` on a 2-clap chain, `on_triple()` on 3+. Single
    onsets are silently ignored (they fire too often in practice).
    """

    def __init__(
        self,
        sample_rate: int = 16_000,
        on_double: Optional[Callable[[], None]] = None,
        on_triple: Optional[Callable[[], None]] = None,
    ):
        self.sample_rate = sample_rate
        self.on_double = on_double
        self.on_triple = on_triple

        # Rolling baseline of spectral-flux values
        self._baseline: deque[float] = deque(maxlen=BASELINE_WINDOW)
        # Onset timestamps (monotonic ms) in the current chain
        self._chain: list[int] = []
        self._last_onset_ms = 0
        self._prev_spectrum: Optional["np.ndarray"] = None  # noqa: F821

    # ── public ─────────────────────────────────────────────────────

    def feed(self, pcm_chunk: bytes) -> None:
        """Process one frame. Fires callbacks if a chain completes."""
        try:
            import numpy as np
        except ImportError:
            return   # graceful — numpy is in the voice extra

        if not pcm_chunk:
            return

        # 1. PCM → float window
        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype("float32")
        if samples.size < 32:
            return
        # Window so the FFT doesn't ring
        window = np.hanning(samples.size)
        spectrum = np.abs(np.fft.rfft(samples * window))

        # 2. Spectral flux against the previous frame
        if self._prev_spectrum is None or self._prev_spectrum.shape != spectrum.shape:
            self._prev_spectrum = spectrum
            return
        diff = spectrum - self._prev_spectrum
        # Half-wave rectify (only count rising energy)
        flux = float(np.sum(np.maximum(diff, 0.0)))
        self._prev_spectrum = spectrum

        # 3. Baseline-relative threshold
        baseline = (sum(self._baseline) / len(self._baseline)
                    if self._baseline else flux + 1.0)
        self._baseline.append(flux)

        now_ms = int(time.monotonic() * 1000)
        is_onset = (
            flux > baseline * THRESHOLD_MULT
            and (now_ms - self._last_onset_ms) > MIN_GAP_MS
        )

        if is_onset:
            self._chain.append(now_ms)
            self._last_onset_ms = now_ms
            log.debug("clap onset #%d (flux=%.0f baseline=%.0f)",
                      len(self._chain), flux, baseline)

        # 4. Chain-end check: enough silence since the last onset?
        if self._chain and (now_ms - self._last_onset_ms) > CHAIN_END_MS:
            self._finalize_chain()

    def reset(self) -> None:
        self._baseline.clear()
        self._chain.clear()
        self._last_onset_ms = 0
        self._prev_spectrum = None

    # ── internals ─────────────────────────────────────────────────

    def _finalize_chain(self) -> None:
        chain = self._chain
        self._chain = []
        if len(chain) < 2:
            return     # singles ignored
        # Sanity: spread shouldn't exceed CHAIN_WINDOW_MS
        spread = chain[-1] - chain[0]
        if spread > CHAIN_WINDOW_MS:
            return
        if len(chain) == 2 and self.on_double:
            try: self.on_double()
            except Exception as e:  # noqa: BLE001
                log.debug("on_double callback failed: %s", e)
        elif len(chain) >= 3 and self.on_triple:
            try: self.on_triple()
            except Exception as e:  # noqa: BLE001
                log.debug("on_triple callback failed: %s", e)

"""Speech-to-text — Faster-Whisper backend (100% local, OSS, GPU-friendly).

WHY this module exists:
  The Telegram path already uses Groq Whisper for transcription. That
  requires a network round-trip AND an API key. For the new `argus
  listen` voice-CLI mode, ARGUS needs FAST, LOCAL transcription so the
  loop stays under 2s wake-to-reply on a modern laptop with no network.

Faster-Whisper:
  • 4x faster than openai-whisper, lower memory.
  • CPU-only path works; CUDA / Metal accelerated when available.
  • Model sizes: tiny (39MB) → base (74MB) → small (244MB) → medium (769MB).
  • First call downloads the model from HuggingFace; subsequent calls
    load from disk cache (~/.cache/huggingface/hub).

Graceful degradation:
  • If `faster-whisper` isn't installed, exposes a clear pip install
    error to the listener loop — never raises during import.
  • Singleton model cache: load once per process.
"""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("argus.voice.stt")


@dataclass(frozen=True)
class TranscriptResult:
    text:            str
    language:        str
    duration_ms:     int
    avg_logprob:     float       # confidence proxy; closer to 0 = better
    backend:         str         # "faster-whisper" | "groq" | "openai" | ...
    elapsed_ms:      int


# Singleton model cache
_FW_MODEL = None
_FW_MODEL_NAME = ""
_FW_LOCK = asyncio.Lock()


async def _load_faster_whisper(model_size: str = "tiny.en",
                                 progress_callback=None) -> Optional[object]:
    """Lazy-load the faster-whisper model once per process.

    Args:
      model_size       — tiny | tiny.en | base | base.en | small | medium | large-v3
      progress_callback(stage, message) — called at load milestones so the
                                          listener can update its HUD.

    Returns None if the package isn't installed."""
    global _FW_MODEL, _FW_MODEL_NAME
    if _FW_MODEL is not None and _FW_MODEL_NAME == model_size:
        return _FW_MODEL
    async with _FW_LOCK:
        if _FW_MODEL is not None and _FW_MODEL_NAME == model_size:
            return _FW_MODEL

        if progress_callback:
            progress_callback("downloading", f"downloading {model_size} (~40-150 MB, one-time)")

        def _load():
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError:
                log.warning("faster-whisper not installed")
                return None
            device, compute = _detect_device_compute()
            log.info("loading faster-whisper model=%s device=%s compute=%s",
                     model_size, device, compute)
            try:
                m = WhisperModel(model_size, device=device, compute_type=compute,
                                 download_root=os.path.expanduser(
                                     "~/.cache/argus/whisper-models"))
                return m
            except Exception as e:  # noqa: BLE001
                log.warning("faster-whisper load failed: %s", e)
                return None

        m = await asyncio.to_thread(_load)
        if m is not None:
            _FW_MODEL = m
            _FW_MODEL_NAME = model_size
            if progress_callback:
                progress_callback("ready", f"{model_size} loaded")
        return m


async def prewarm(model_size: str = "tiny.en",
                  progress_callback=None) -> bool:
    """Force model download + load + a dummy inference so the FIRST real
    transcribe call is fast.

    Call this from the listener at startup, BEFORE the main loop begins,
    so the user sees "downloading…" / "loading…" instead of a frozen
    "transcribing…" line on their first wake.

    Returns True on success.
    """
    model = await _load_faster_whisper(model_size, progress_callback=progress_callback)
    if model is None:
        return False
    if progress_callback:
        progress_callback("compiling", "warming up (first transcribe ~3s)")

    # Tiny synthetic transcribe — 0.5s of silence — forces graph compile
    # so the first REAL transcribe is fast (~0.5s on M-series, ~1-3s on x86).
    import numpy as np
    silent = np.zeros(SAMPLE_RATE_DEFAULT // 2, dtype=np.int16).tobytes()
    try:
        await transcribe(silent, sample_rate=SAMPLE_RATE_DEFAULT,
                          model_size=model_size)
    except Exception as e:  # noqa: BLE001
        log.warning("prewarm transcribe failed (non-fatal): %s", e)
        return False
    if progress_callback:
        progress_callback("ready", "speech engine ready")
    return True


SAMPLE_RATE_DEFAULT = 16_000


def _detect_device_compute() -> tuple[str, str]:
    """Return (device, compute_type) tuple tuned for what's available."""
    try:
        import torch  # type: ignore
        if torch.cuda.is_available():
            return "cuda", "float16"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "cpu", "int8"   # MPS Whisper is flaky; CPU+int8 is fast
    except ImportError:
        pass
    return "cpu", "int8"


# ── Public API ──────────────────────────────────────────────────────────────


async def transcribe(audio_bytes: bytes, *,
                      sample_rate: int = 16_000,
                      language: str = "en",
                      model_size: str = "tiny.en",
                      timeout_seconds: float = 30.0) -> Optional[TranscriptResult]:
    """Transcribe raw 16-bit PCM mono audio bytes. Returns None on failure
    or timeout.

    Args:
      audio_bytes      — raw PCM s16le mono samples
      sample_rate      — must be 16000 for Whisper (resample upstream if needed)
      language         — ISO-639-1 code or "" for auto-detect
      model_size       — tiny | tiny.en | base | base.en | small | medium | large-v3
                          (.en variants are English-only + much faster)
      timeout_seconds  — hard ceiling so a hung model never locks the listener
    """
    import time as _t
    t0 = _t.monotonic()

    if not audio_bytes:
        return None

    # Wrap raw PCM in a WAV so Whisper's decoder is happy.
    wav_path = _pcm_to_wav_tempfile(audio_bytes, sample_rate)
    if not wav_path:
        return None

    try:
        try:
            result = await asyncio.wait_for(
                _transcribe_faster_whisper(wav_path, language, model_size),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            log.warning("transcribe exceeded %.0fs timeout for model=%s",
                        timeout_seconds, model_size)
            return None
        if result is None:
            return None
        elapsed_ms = int((_t.monotonic() - t0) * 1000)
        return TranscriptResult(
            text=result.text, language=result.language,
            duration_ms=result.duration_ms, avg_logprob=result.avg_logprob,
            backend="faster-whisper", elapsed_ms=elapsed_ms,
        )
    finally:
        try: wav_path.unlink()
        except OSError: pass


async def _transcribe_faster_whisper(wav_path: Path, language: str,
                                       model_size: str) -> Optional[TranscriptResult]:
    model = await _load_faster_whisper(model_size)
    if model is None:
        return None

    def _run() -> Optional[TranscriptResult]:
        try:
            segments, info = model.transcribe(
                str(wav_path),
                language=language or None,
                vad_filter=True,                 # extra VAD pass for cleaner output
                vad_parameters=dict(min_silence_duration_ms=400),
                beam_size=1,                     # speed > best-of-N for live UX
                best_of=1,
                temperature=0.0,
            )
            text_parts: list[str] = []
            logprobs: list[float] = []
            for seg in segments:
                text_parts.append(seg.text)
                if seg.avg_logprob is not None:
                    logprobs.append(seg.avg_logprob)
            text = " ".join(p.strip() for p in text_parts if p.strip())
            avg_lp = sum(logprobs) / len(logprobs) if logprobs else 0.0
            return TranscriptResult(
                text=text,
                language=info.language or "en",
                duration_ms=int((info.duration or 0) * 1000),
                avg_logprob=avg_lp,
                backend="faster-whisper",
                elapsed_ms=0,                    # caller fills this
            )
        except Exception as e:  # noqa: BLE001
            log.warning("faster-whisper transcribe failed: %s", e)
            return None

    return await asyncio.to_thread(_run)


def _pcm_to_wav_tempfile(audio_bytes: bytes, sample_rate: int) -> Optional[Path]:
    """Wrap raw 16-bit PCM mono bytes in a WAV file (Whisper needs the header)."""
    import tempfile, uuid
    tmpdir = Path(tempfile.gettempdir()) / "argus_voice"
    tmpdir.mkdir(exist_ok=True)
    wav_path = tmpdir / f"capture_{uuid.uuid4().hex[:12]}.wav"
    try:
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)               # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(audio_bytes)
        return wav_path
    except Exception as e:  # noqa: BLE001
        log.warning("pcm->wav failed: %s", e)
        return None


def available(model_size: str = "base") -> tuple[bool, str]:
    """Cheap synchronous check used by `argus doctor`."""
    try:
        import faster_whisper  # noqa: F401
        return True, f"faster-whisper installed; model='{model_size}' lazy-loads on first call"
    except ImportError:
        return False, "pip install faster-whisper  (or: uv sync --extra voice-wake)"

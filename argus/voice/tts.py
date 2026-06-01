"""Text-to-speech — multi-provider, Supertonic-first.

Hermes pattern: a single `synthesize()` dispatch picks the configured
provider, writes a WAV to a temp file, and (when ffmpeg is available)
transcodes to OGG/Opus for Telegram voice-bubble UX.

Providers shipped here:
  • supertonic — local, free, MIT, ~99M params CPU-friendly (DEFAULT)
  • edge       — Microsoft Edge neural TTS, free, no API key
  • openai     — gpt-4o-mini-tts (needs OPENAI_API_KEY or VOICE_OPENAI_KEY)
  • elevenlabs — eleven_multilingual_v2 (needs ELEVENLABS_API_KEY)
  • piper      — local, voices auto-download
  • none       — disabled

Each provider is lazy-imported so a missing optional dep doesn't break
the rest of ARGUS at import time.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from argus import config as _config


@dataclass(frozen=True)
class TTSResult:
    path: Path                # absolute path on disk
    format: str               # "ogg" | "mp3" | "wav"
    duration_ms: int
    provider: str
    voice: str


# ── Public API ────────────────────────────────────────────────────────────────


def list_providers() -> list[str]:
    return ["supertonic", "edge", "openai", "elevenlabs", "piper", "none"]


def available(provider: str) -> tuple[bool, str]:
    """Return (installed, detail) — does this provider have what it needs?"""
    p = provider.lower().strip()
    if p == "none":
        return True, "TTS disabled"
    if p == "supertonic":
        try:
            import supertonic  # noqa: F401
            return True, "supertonic ✓"
        except ImportError:
            return False, "pip install supertonic"
    if p == "edge":
        try:
            import edge_tts  # noqa: F401
            return True, "edge-tts ✓"
        except ImportError:
            return False, "pip install edge-tts"
    if p == "openai":
        key = os.environ.get("VOICE_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY")
        return (bool(key), "OPENAI_API_KEY set" if key else "set OPENAI_API_KEY in ~/.argus/.env")
    if p == "elevenlabs":
        key = os.environ.get("ELEVENLABS_API_KEY")
        return (bool(key), "ELEVENLABS_API_KEY set" if key else "set ELEVENLABS_API_KEY in ~/.argus/.env")
    if p == "piper":
        return (shutil.which("piper") is not None, "piper CLI on PATH" if shutil.which("piper") else "brew install piper / pip install piper-tts")
    return False, f"unknown provider '{provider}'"


async def synthesize(
    text: str,
    *,
    cfg: Optional[_config.Config] = None,
    voice: Optional[str] = None,
    speed: Optional[float] = None,
    lang: Optional[str] = None,
    output_format: str = "ogg",  # ogg (Telegram voice-bubble) | mp3 | wav
) -> Optional[TTSResult]:
    """Synthesize speech to a temp file. Returns None on failure.

    Calls the provider configured in cfg.voice.tts_provider. Falls back to
    'edge' if the configured provider isn't installed, then 'none'.
    """
    cfg = cfg or _config.load()
    if cfg.voice.tts_provider == "none":
        return None

    provider = cfg.voice.tts_provider.lower().strip()
    voice = voice or cfg.voice.tts_voice
    speed = speed if speed is not None else cfg.voice.tts_speed
    lang  = lang or cfg.voice.tts_lang

    text = _strip_markdown_for_tts(text)
    if not text.strip():
        return None

    # Order: configured → edge → openai. Keep trying until one works.
    chain = [provider]
    if provider != "edge":   chain.append("edge")
    if provider != "openai": chain.append("openai")

    last_err = None
    for p in chain:
        ok, _ = available(p)
        if not ok:
            continue
        try:
            wav_path = await _dispatch(p, text, voice, speed, lang)
            if not wav_path:
                continue
            final = _transcode(wav_path, output_format)
            actual_format = final.suffix.lstrip(".").lower() or output_format
            return TTSResult(
                path=final,
                format=actual_format,
                duration_ms=_audio_duration_ms(final),
                provider=p,
                voice=voice,
            )
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    if last_err:
        print(f"[tts] all providers failed; last error: {last_err}")
    return None


# ── Provider dispatch ─────────────────────────────────────────────────────────


async def _dispatch(provider: str, text: str, voice: str, speed: float, lang: str) -> Optional[Path]:
    if provider == "supertonic":  return await _tts_supertonic(text, voice, speed, lang)
    if provider == "edge":        return await _tts_edge(text, voice, speed, lang)
    if provider == "openai":      return await _tts_openai(text, voice, speed)
    if provider == "elevenlabs":  return await _tts_elevenlabs(text, voice)
    if provider == "piper":       return await _tts_piper(text, voice)
    return None


async def _tts_supertonic(text: str, voice: str, speed: float, lang: str) -> Optional[Path]:
    """Supertonic — local CPU-only, 44.1 kHz WAV, ~70x real-time on M-series."""
    # Heavy-init in a thread; subsequent calls reuse the cached singleton.
    tts = await asyncio.to_thread(_supertonic_instance)
    # Voice name fallback: Supertonic v1 uses M1–M5/F1–F5; bad name → first valid.
    try:
        style = await asyncio.to_thread(tts.get_voice_style, voice_name=voice)
    except Exception:
        fallback_name = (tts.voice_style_names() if callable(getattr(tts, "voice_style_names", None))
                         else "M1")
        if isinstance(fallback_name, (list, tuple)) and fallback_name:
            fallback_name = fallback_name[0]
        style = await asyncio.to_thread(tts.get_voice_style, voice_name=fallback_name)

    def _run():
        result = tts.synthesize(text=text, voice_style=style, lang=lang)
        # Supertonic v1 returns (audio_ndarray, duration_ndarray); audio is (1, N) float32.
        wav = result[0] if isinstance(result, tuple) else result
        out = _tmp_path("wav")
        # Prefer the library's own writer (handles shape/dtype quirks across versions).
        if hasattr(tts, "save_audio"):
            tts.save_audio(wav, str(out))
            return out
        # Fallback: soundfile direct write — squeeze to 1D.
        import soundfile as sf
        import numpy as np
        flat = np.asarray(wav).squeeze()
        sample_rate = getattr(tts, "sample_rate", 44100) or 44100
        sf.write(str(out), flat, int(sample_rate), format="WAV")
        return out

    return await asyncio.to_thread(_run)


_supertonic_cached = None


def _supertonic_instance():
    """Cache the heavy TTS object across calls (model weights loaded once)."""
    global _supertonic_cached
    if _supertonic_cached is None:
        from supertonic import TTS  # type: ignore
        _supertonic_cached = TTS(auto_download=True)
    return _supertonic_cached


async def _tts_edge(text: str, voice: str, speed: float, lang: str) -> Optional[Path]:
    """Microsoft Edge neural TTS — free, MP3 output, no API key."""
    import edge_tts  # type: ignore

    # Map Supertonic-style voice IDs to Edge defaults if user kept defaults.
    edge_voice = voice if "-" in voice and "Neural" in voice else "en-US-AriaNeural"
    rate_str = f"{int((speed - 1) * 100):+d}%"

    out = _tmp_path("mp3")
    communicate = edge_tts.Communicate(text=text, voice=edge_voice, rate=rate_str)
    await communicate.save(str(out))
    return out


async def _tts_openai(text: str, voice: str, speed: float) -> Optional[Path]:
    """OpenAI gpt-4o-mini-tts — needs VOICE_OPENAI_KEY or OPENAI_API_KEY."""
    import httpx

    key = os.environ.get("VOICE_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None

    # Map any voice name to OpenAI's six built-in voices.
    valid = {"alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer"}
    oa_voice = voice if voice.lower() in valid else "alloy"

    out = _tmp_path("mp3")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": "gpt-4o-mini-tts", "input": text,
                  "voice": oa_voice, "speed": speed, "response_format": "mp3"},
        )
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


async def _tts_elevenlabs(text: str, voice: str) -> Optional[Path]:
    import httpx
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        return None
    # Default to Adam if a non-Eleven voice ID was passed.
    voice_id = voice if len(voice) > 10 else "pNInz6obpgDQGcFmaJgB"
    out = _tmp_path("mp3")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_multilingual_v2"},
        )
        r.raise_for_status()
        out.write_bytes(r.content)
    return out


async def _tts_piper(text: str, voice: str) -> Optional[Path]:
    """Piper — local CLI, tiny, fast, robotic. Last-resort fallback."""
    piper = shutil.which("piper")
    if not piper:
        return None
    out = _tmp_path("wav")
    piper_voice = voice if "/" in voice else "en_US-lessac-medium"

    def _run():
        proc = subprocess.run(
            [piper, "--model", piper_voice, "--output_file", str(out)],
            input=text.encode("utf-8"),
            capture_output=True, timeout=30,
        )
        return out if proc.returncode == 0 else None

    return await asyncio.to_thread(_run)


# ── Helpers ───────────────────────────────────────────────────────────────────


_TMP_DIR = Path(tempfile.gettempdir()) / "argus_voice"


def _tmp_path(ext: str) -> Path:
    _TMP_DIR.mkdir(exist_ok=True)
    return _TMP_DIR / f"tts_{uuid.uuid4().hex[:12]}.{ext}"


def _transcode(src: Path, target_ext: str) -> Path:
    """Convert src → target. Tries:
       1. ffmpeg (if installed) — best quality, supports all formats
       2. pure-Python lameenc — for WAV → MP3 when ffmpeg is missing
       3. no-op — return source unchanged if no path works

    Telegram requires either OGG/Opus (voice bubble) or MP3 (audio bubble).
    Since ffmpeg is the only way to make OGG/Opus, we fall back to MP3 for
    the Telegram path so the user never sees a raw WAV file.
    """
    src_ext = src.suffix.lstrip(".").lower()
    target_ext = target_ext.lower()
    if src_ext == target_ext:
        return src

    # ── Path 1: ffmpeg ──────────────────────────────────────────────
    if shutil.which("ffmpeg"):
        dst = src.with_suffix(f".{target_ext}")
        codec_args = []
        if target_ext == "ogg":
            codec_args = ["-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-ar", "48000"]
        elif target_ext == "mp3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", "128k"]
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), *codec_args, str(dst)],
                capture_output=True, timeout=30, check=True,
            )
            try: src.unlink()
            except OSError: pass
            return dst
        except (subprocess.SubprocessError, subprocess.TimeoutExpired):
            pass  # fall through to other paths

    # ── Path 2: pure-Python WAV → MP3 via lameenc ──────────────────
    if src_ext == "wav" and target_ext == "mp3":
        mp3_path = _wav_to_mp3_pure_python(src)
        if mp3_path:
            return mp3_path

    # ── Path 3: give up; caller handles raw format ─────────────────
    return src


def _wav_to_mp3_pure_python(wav_path: Path) -> Optional[Path]:
    """Convert WAV → MP3 without ffmpeg using `lameenc` (pure-Python LAME).

    Returns the MP3 path on success, None on any failure.
    Used by the Telegram gateway so users always get an MP3 audio bubble,
    never a raw WAV file.
    """
    try:
        import lameenc
        import soundfile as sf
        import numpy as np
    except ImportError:
        return None

    try:
        # Read WAV — soundfile gives float32 in [-1, 1] by default
        data, sample_rate = sf.read(str(wav_path), dtype="int16")
        # Mono → keep 1-D; stereo → keep 2-D (frames × channels)
        if data.ndim == 1:
            channels = 1
            pcm_bytes = data.tobytes()
        else:
            channels = data.shape[1]
            pcm_bytes = data.tobytes()

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(int(sample_rate))
        encoder.set_channels(channels)
        encoder.set_quality(2)   # 2 = highest quality

        mp3_data = encoder.encode(pcm_bytes)
        mp3_data += encoder.flush()

        mp3_path = wav_path.with_suffix(".mp3")
        mp3_path.write_bytes(mp3_data)

        # Clean up the source WAV — caller only needs the MP3
        try: wav_path.unlink()
        except OSError: pass
        return mp3_path
    except Exception:
        return None


def _audio_duration_ms(path: Path) -> int:
    """Best-effort duration via ffprobe; 0 if unavailable."""
    if not shutil.which("ffprobe"):
        return 0
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=5,
        )
        return int(float(out.stdout.strip()) * 1000)
    except (subprocess.SubprocessError, ValueError):
        return 0


_MD_RE = None


def _strip_markdown_for_tts(text: str) -> str:
    """Strip markdown so the voice doesn't read 'asterisk asterisk bold asterisk asterisk'."""
    import re
    global _MD_RE
    if _MD_RE is None:
        _MD_RE = re.compile(
            r"(```.+?```|`[^`]+`|\*{1,3}|_{1,3}|#+\s|>\s|\[([^\]]+)\]\([^)]+\))",
            re.DOTALL,
        )
    def _sub(m):
        # Keep link text, drop URL
        return m.group(2) or ""
    cleaned = _MD_RE.sub(_sub, text)
    # Collapse whitespace
    return " ".join(cleaned.split())

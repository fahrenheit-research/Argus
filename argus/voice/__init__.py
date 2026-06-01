"""Voice subsystem — TTS + STT + wake-word + voice-listener.

Hermes-style multi-provider TTS with Supertonic (local, free, MIT) as
the default. Faster-Whisper for local STT. OpenWakeWord for hands-free
"Hey Argus" activation in `argus listen`.

Public exports:
  TTS:        synthesize, TTSResult, list_providers, available
  STT:        transcribe, TranscriptResult            (lazy import)
  Listener:   run_listener                            (lazy import)
"""

from argus.voice.tts import synthesize, TTSResult, list_providers, available

__all__ = [
    "synthesize", "TTSResult", "list_providers", "available",
    "transcribe", "TranscriptResult", "run_listener",
]


# Lazy re-exports — sounddevice/faster-whisper/openwakeword shouldn't be
# loaded just because someone imports argus.voice (e.g. from the chat REPL).
def __getattr__(name: str):
    if name in ("transcribe", "TranscriptResult"):
        from argus.voice import stt as _stt
        return getattr(_stt, name)
    if name == "run_listener":
        from argus.voice.listener import run_listener as _rl
        return _rl
    raise AttributeError(f"module 'argus.voice' has no attribute {name!r}")

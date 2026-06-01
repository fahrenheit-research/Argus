"""Wake-word detection — OpenWakeWord (OSS, ONNX-based, fully local).

WHY this module exists:
  Hands-free activation. The user says "Hey Argus" (or any other phrase
  with a trained model) and the listener loop transitions from ASLEEP
  to LISTENING without a keypress.

Model resolution order for `Detector(name)`:
  1. ~/.argus/wake_models/<name>.onnx          ← user-trained custom model
  2. ./wake_models/<name>.onnx                  ← project-local override
  3. OpenWakeWord pre-trained name              ← fallback (hey_jarvis, alexa, …)

So `Detector("hey_argus")` does the right thing both before and after
the user trains their own model:
  • Before training → falls through to OWW's `hey_jarvis` (closest
    phonetic match for "Hey Argus", ~85% recall).
  • After training → loads ~/.argus/wake_models/hey_argus.onnx.

Graceful degradation:
  • If openwakeword isn't installed, `Detector.is_available()` returns
    False — the listener falls back to "always-listening" + VAD-only.
  • Heavy ONNX/transformers deps are LAZY-loaded.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("argus.voice.wakeword")


# Singletons (one model per process per name)
_OWW_MODELS: dict[str, object] = {}
_OWW_THRESHOLD = 0.5

# Pre-trained models that ship with openwakeword.
# Used to decide whether to pass a name OR a filesystem path to Model().
_OWW_BUILTIN = frozenset({
    "alexa", "hey_jarvis", "hey_mycroft", "ok_nabu", "timer", "weather",
    # alternative spellings + variants
    "hey jarvis", "hey mycroft", "ok nabu",
})


def _resolve_model_path(name: str) -> tuple[str, str]:
    """Resolve a wake-word `name` to either a filesystem path or an OWW
    built-in name. Returns (resolved_arg, how) where `how` is one of:
       "custom-user"    → ~/.argus/wake_models/<name>.onnx exists
       "custom-project" → ./wake_models/<name>.onnx exists
       "builtin"        → OWW prebuilt model (used by name)
       "fallback"       → user asked for a custom name we don't have;
                          falling back to hey_jarvis (closest match)
    """
    # 1. User custom model
    user_path = Path(os.path.expanduser(f"~/.argus/wake_models/{name}.onnx"))
    if user_path.exists():
        return str(user_path), "custom-user"

    # 2. Project-local model
    project_path = Path(f"./wake_models/{name}.onnx")
    if project_path.exists():
        return str(project_path.resolve()), "custom-project"

    # 3. OWW built-in
    normalised = name.lower().replace(" ", "_").replace("-", "_")
    if normalised in _OWW_BUILTIN:
        return normalised, "builtin"

    # 4. Fallback — user asked for something we don't have. Closest
    #    phonetic match for "hey argus" is "hey_jarvis".
    log.warning("wake-word '%s' not found - falling back to hey_jarvis "
                "(closest pretrained match for 'Hey Argus'). Train a "
                "custom one with desktop/wake_word_training/", name)
    return "hey_jarvis", "fallback"


def _load_oww(model_name: str = "hey_jarvis") -> tuple[Optional[object], str]:
    """Lazy-load openwakeword. Returns (model, source_tag).
       source_tag describes which model file/name was actually loaded
       so callers can log it for the user.

       AUTO-DOWNLOADS the prebuilt ONNX models on first call (~10MB).
       This avoids users seeing the cryptic 'File doesn't exist' error
       on a fresh install — they just have to wait a few seconds the
       first time `argus listen` runs."""
    resolved, how = _resolve_model_path(model_name)
    cache_key = f"{model_name}::{resolved}"
    if cache_key in _OWW_MODELS:
        return _OWW_MODELS[cache_key], how
    try:
        from openwakeword.model import Model  # type: ignore
        from openwakeword import utils as _oww_utils  # type: ignore

        # First-run download — only triggers if the prebuilt ONNXes are
        # missing from openwakeword/resources/models/.
        _ensure_oww_models_downloaded(resolved, how)

        log.info("loading openwakeword: name=%s resolved=%s (%s)",
                 model_name, resolved, how)
        m = Model(wakeword_models=[resolved], inference_framework="onnx")
        _OWW_MODELS[cache_key] = m
        return m, how
    except ImportError:
        log.info("openwakeword not installed - wake-word path disabled")
        return None, "missing"
    except Exception as e:  # noqa: BLE001
        log.warning("openwakeword load failed: %s", e)
        return None, "error"


_OWW_DOWNLOAD_DONE = False


def _ensure_oww_models_downloaded(resolved: str, how: str) -> None:
    """Make sure the prebuilt OWW ONNX models exist on disk before the
    first Model() instantiation. Idempotent + cheap after the first call.

    Custom .onnx paths (`how` in {custom-user, custom-project}) skip
    this — they don't depend on the prebuilt cache.
    """
    global _OWW_DOWNLOAD_DONE
    if _OWW_DOWNLOAD_DONE:
        return
    if how in ("custom-user", "custom-project"):
        _OWW_DOWNLOAD_DONE = True
        return
    try:
        # Check whether the resolved built-in model already exists on disk.
        # openwakeword stores them inside the package's resources/models/.
        import openwakeword as _oww_pkg
        from pathlib import Path as _Path
        models_dir = _Path(_oww_pkg.__file__).parent / "resources" / "models"
        # Filename convention: <name>_v0.1.onnx for the built-ins.
        expected = models_dir / f"{resolved}_v0.1.onnx"
        if expected.exists():
            _OWW_DOWNLOAD_DONE = True
            return

        # Download — prints a one-line "downloading…" since this can take a few seconds.
        import sys as _sys
        _sys.stderr.write("⟨◇⟩ openwakeword: downloading prebuilt models (one-time, ~10 MB)…\n")
        _sys.stderr.flush()
        from openwakeword.utils import download_models
        download_models()
        _sys.stderr.write("⟨◇⟩ openwakeword: models ready.\n")
        _sys.stderr.flush()
    except Exception as e:  # noqa: BLE001
        log.warning("openwakeword model download failed: %s — wake-word "
                    "may not work until you run: "
                    "python -c 'from openwakeword.utils import download_models; download_models()'", e)
    finally:
        _OWW_DOWNLOAD_DONE = True


class Detector:
    """Stateful wake-word detector.

    Usage:
        det = Detector("hey_jarvis", threshold=0.5)
        for chunk in mic_stream():       # 80ms chunks of int16 PCM @ 16kHz
            if det.feed(chunk):
                # Wake word triggered. Reset and start full capture.
                det.reset()
                ...

    If openwakeword isn't installed, `feed()` always returns False and
    `is_available()` returns False — caller should fall back to a
    non-wake-word activation (e.g. VAD-only or push-to-talk).
    """

    def __init__(self, model_name: str = "hey_argus", threshold: float = 0.5):
        self.model_name = model_name
        self.threshold = float(threshold)
        self._model, self._source = _load_oww(model_name)
        self._consecutive_hits = 0

    def is_available(self) -> bool:
        return self._model is not None

    @property
    def source(self) -> str:
        """How the model was resolved: 'custom-user' | 'custom-project' |
        'builtin' | 'fallback' | 'missing' | 'error'."""
        return self._source

    def describe(self) -> str:
        """Human-readable: e.g. 'hey_argus (custom-user, ~/.argus/wake_models/)'
        or 'hey_argus → hey_jarvis (fallback, train custom for best)'."""
        if self._source == "missing":
            return f"{self.model_name} (openwakeword not installed)"
        if self._source == "custom-user":
            return f"{self.model_name} (custom model from ~/.argus/wake_models/)"
        if self._source == "custom-project":
            return f"{self.model_name} (project-local custom model)"
        if self._source == "builtin":
            return f"{self.model_name} (OWW pretrained)"
        if self._source == "fallback":
            return f"{self.model_name} → hey_jarvis (fallback - train custom for better recall)"
        return f"{self.model_name} ({self._source})"

    def feed(self, pcm_chunk: bytes) -> bool:
        """Feed one 80ms chunk (1280 samples @ 16kHz, int16 little-endian).
        Returns True on wake-word detection."""
        if self._model is None:
            return False
        try:
            arr = np.frombuffer(pcm_chunk, dtype=np.int16)
            scores = self._model.predict(arr)
            # `scores` is {model_name: confidence}
            top = max(scores.values()) if scores else 0.0
            if top >= self.threshold:
                self._consecutive_hits += 1
                # Two hits in a row = real wake (reduces false-positives)
                if self._consecutive_hits >= 2:
                    log.info("wake word: %s (score=%.2f)", self.model_name, top)
                    return True
            else:
                self._consecutive_hits = 0
        except Exception as e:  # noqa: BLE001
            log.debug("wake-word feed error: %s", e)
        return False

    def reset(self) -> None:
        self._consecutive_hits = 0
        if self._model is not None and hasattr(self._model, "reset"):
            try: self._model.reset()
            except Exception: pass

"""Embedding adapters.

Default: local SentenceTransformers all-MiniLM-L6-v2 (86 MB, 384-dim, fast).
Optional: OpenAI text-embedding-3-small (1536-dim) if OPENAI_API_KEY set.

The local model is loaded lazily on first embed() call (~2s cold start),
then cached for the process lifetime.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("argus.vault.embed")

# Dimensions of supported embedding models — Vault stores the dim alongside
# each vector so it never tries to compare vectors of different sizes.
EMBED_DIMS: dict[str, int] = {
    "minilm":           384,
    "openai-3-small":  1536,
    "openai-3-large":  3072,
}

DEFAULT_MODEL = os.environ.get("ARGUS_VAULT_EMBED", "minilm")


class Embedder:
    """Pluggable embedding backend.

    Usage:
        emb = Embedder()          # default local minilm
        vec = emb.embed("hello")  # → np.ndarray of shape (dim,)
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model_name = model
        self.dim        = EMBED_DIMS.get(model, 384)
        self._st_model  = None   # lazy
        self._oa_client = None   # lazy

    # ── Public ──────────────────────────────────────────────────────────────

    def embed(self, text: str) -> "np.ndarray":
        import numpy as np
        text = (text or "").strip()
        if not text:
            return np.zeros(self.dim, dtype="float32")

        if self.model_name == "minilm":
            return self._embed_local(text)
        if self.model_name.startswith("openai-"):
            return self._embed_openai(text)
        raise ValueError(f"unknown embed model: {self.model_name}")

    def embed_batch(self, texts: list[str]) -> "np.ndarray":
        """Batched embeddings. Returns shape (n, dim)."""
        import numpy as np
        cleaned = [(t or "").strip() for t in texts]
        if self.model_name == "minilm":
            model = self._load_minilm()
            return np.asarray(model.encode(cleaned, normalize_embeddings=True),
                              dtype="float32")
        # Fallback: per-item
        return np.vstack([self.embed(t) for t in cleaned])

    # ── Backends ────────────────────────────────────────────────────────────

    def _load_minilm(self):
        if self._st_model is not None:
            return self._st_model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "sentence-transformers not installed. Run: uv sync --extra vault"
            ) from e
        log.info("loading sentence-transformers/all-MiniLM-L6-v2 (~86 MB) …")
        self._st_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        return self._st_model

    def _embed_local(self, text: str) -> "np.ndarray":
        import numpy as np
        model = self._load_minilm()
        v = model.encode([text], normalize_embeddings=True)[0]
        return np.asarray(v, dtype="float32")

    def _embed_openai(self, text: str) -> "np.ndarray":
        import numpy as np
        try:
            import openai  # type: ignore
        except ImportError as e:
            raise RuntimeError("openai SDK not installed for OpenAI embeddings") from e
        if self._oa_client is None:
            self._oa_client = openai.OpenAI()
        model_id = {
            "openai-3-small": "text-embedding-3-small",
            "openai-3-large": "text-embedding-3-large",
        }[self.model_name]
        r = self._oa_client.embeddings.create(input=text, model=model_id)
        return np.asarray(r.data[0].embedding, dtype="float32")

"""Vault store — SQLite-backed memory with vector recall.

Schema:

  entries
    id          INTEGER PRIMARY KEY
    text        TEXT NOT NULL         -- the actual memory content
    kind        TEXT NOT NULL         -- 'fact' | 'snippet' | 'event' | 'user'
    source      TEXT                  -- e.g. 'cron:job1' | 'chat:session42'
    tags        TEXT                  -- comma-separated
    created_at  TEXT NOT NULL
    model       TEXT NOT NULL         -- embedding model id
    dim         INTEGER NOT NULL      -- vector dimensionality

  vec_index (sqlite-vec virtual table; created lazily if extension loads)
    rowid       INTEGER       (= entries.id)
    embedding   FLOAT[<dim>]

Fallback path: if sqlite-vec doesn't load (Linux distros without vec0.so),
we store the raw float32 bytes in `entries.embedding_blob` and brute-force
top-k via numpy. Slower per query but still <50ms for ~10k rows.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("argus.vault.store")

DB_PATH = Path(os.path.expanduser("~/.argus/vault.db"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VaultEntry:
    id:          int
    text:        str
    kind:        str
    source:      str
    tags:        str
    created_at:  str
    score:       float = 0.0    # populated only on recall()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    text            TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'fact',
    source          TEXT,
    tags            TEXT,
    created_at      TEXT NOT NULL,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    embedding_blob  BLOB
);

CREATE INDEX IF NOT EXISTS idx_entries_kind   ON entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source);
CREATE INDEX IF NOT EXISTS idx_entries_tags   ON entries(tags);
"""


def _pack_floats(vec) -> bytes:
    """float32 array → packed bytes."""
    import numpy as np
    arr = np.asarray(vec, dtype="float32").ravel()
    return arr.tobytes()


def _unpack_floats(blob: bytes, dim: int):
    import numpy as np
    return np.frombuffer(blob, dtype="float32", count=dim)


class Vault:
    """The ARGUS Vault — call remember() / recall() / forget()."""

    def __init__(self, path: Path = DB_PATH, *,
                  embedder=None, vec_backend: str = "auto") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path           = path
        self._embedder      = embedder
        self._has_vec_ext   = False
        self._vec_backend   = vec_backend  # "auto" | "vec0" | "numpy"
        self._init()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)
            # Try to load sqlite-vec extension
            if self._vec_backend in ("auto", "vec0"):
                self._has_vec_ext = self._try_load_vec(c)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.enable_load_extension(True)
        except (sqlite3.OperationalError, AttributeError):
            pass   # macOS python sometimes lacks this; we'll fall back to numpy
        return c

    def _try_load_vec(self, conn: sqlite3.Connection) -> bool:
        """Try to load sqlite-vec extension. Return True on success."""
        try:
            import sqlite_vec   # type: ignore
            sqlite_vec.load(conn)
            log.info("sqlite-vec loaded — using native vector index")
            return True
        except Exception as e:
            log.info("sqlite-vec not available (%s) — using numpy fallback", e)
            return False

    @property
    def embedder(self):
        if self._embedder is None:
            from argus.vault.embed import Embedder
            self._embedder = Embedder()
        return self._embedder

    # ── Public API ──────────────────────────────────────────────────────────

    def remember(self, text: str, *, kind: str = "fact",
                  source: str = "", tags: str = "") -> int:
        """Add a memory. Returns the entry id."""
        if not text or not text.strip():
            raise ValueError("cannot remember an empty string")
        emb = self.embedder
        vec = emb.embed(text.strip())
        with self._conn() as c:
            cur = c.execute(
                """INSERT INTO entries (text, kind, source, tags, created_at,
                       model, dim, embedding_blob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (text.strip(), kind, source, tags, _now_iso(),
                 emb.model_name, emb.dim, _pack_floats(vec)))
            entry_id = cur.lastrowid
        log.debug("remembered #%d (%s): %r", entry_id, kind, text[:60])
        return entry_id

    def recall(self, query: str, *, k: int = 5,
                kind: Optional[str] = None,
                min_score: float = 0.0) -> list[VaultEntry]:
        """Top-k most relevant memories.

        Cosine similarity in [0, 1] range (since embeddings are normalised).
        Pass `kind` to filter (e.g., only 'fact' or 'event').
        """
        import numpy as np
        emb = self.embedder
        q_vec = emb.embed(query)

        with self._conn() as c:
            sql    = "SELECT * FROM entries WHERE dim = ? AND model = ?"
            params: tuple = (emb.dim, emb.model_name)
            if kind:
                sql += " AND kind = ?"
                params = (*params, kind)
            rows = c.execute(sql, params).fetchall()

        if not rows:
            return []

        scored: list[tuple[float, VaultEntry]] = []
        for r in rows:
            v = _unpack_floats(r["embedding_blob"], r["dim"])
            # Cosine since both q_vec and v are normalised (minilm = True,
            # OpenAI = True by spec). For un-normalised models add /norm.
            score = float(np.dot(q_vec, v))
            if score < min_score:
                continue
            scored.append((score, VaultEntry(
                id         = r["id"],
                text       = r["text"],
                kind       = r["kind"],
                source     = r["source"] or "",
                tags       = r["tags"] or "",
                created_at = r["created_at"],
                score      = score,
            )))

        scored.sort(key=lambda t: -t[0])
        return [e for _, e in scored[:k]]

    def forget(self, entry_id: int) -> bool:
        """Delete a single memory by id. Returns True if it existed."""
        with self._conn() as c:
            cur = c.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    def all(self, *, kind: Optional[str] = None,
              limit: int = 1000) -> list[VaultEntry]:
        with self._conn() as c:
            sql, params = "SELECT * FROM entries", ()
            if kind:
                sql += " WHERE kind = ?"
                params = (kind,)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params = (*params, limit)
            return [VaultEntry(
                id=r["id"], text=r["text"], kind=r["kind"],
                source=r["source"] or "", tags=r["tags"] or "",
                created_at=r["created_at"],
            ) for r in c.execute(sql, params).fetchall()]

    def stats(self) -> dict:
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            by_kind = dict(c.execute(
                "SELECT kind, COUNT(*) FROM entries GROUP BY kind").fetchall())
            size_bytes = self.path.stat().st_size if self.path.exists() else 0
        return {
            "entries":     n,
            "by_kind":     by_kind,
            "size_bytes":  size_bytes,
            "vec_backend": "sqlite-vec" if self._has_vec_ext else "numpy",
            "embedder":    self.embedder.model_name,
            "dim":         self.embedder.dim,
            "path":        str(self.path),
        }

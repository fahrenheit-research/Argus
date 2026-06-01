"""ARGUS Vault — local-first SQLite + vector memory store.

A single SQLite file at ~/.argus/vault.db holds:
  • structured tables  (facts, snippets, sources)
  • vector embeddings  (sqlite-vec extension; falls back to brute-force np)

Why this and not Chroma / Pinecone / Convex / pgvector?
  • Zero-service.  No daemon, no Docker, no account.
  • Single-file.   Backup = copy one file. Sync = upload one file.
  • Embedded.      No network round-trip; recall is <5ms.
  • Tiny.          ~500KB of sqlite-vec, 86MB of MiniLM, no node_modules.
  • Apache-2.0 + MIT throughout the dep chain.

APIs:
    from argus.vault import Vault
    v = Vault()
    v.remember("Aniket's preferred voice is British male, deep tone")
    matches = v.recall("voice settings")
    print(matches[0].text, matches[0].score)
"""
from argus.vault.store import Vault, VaultEntry
from argus.vault.embed  import Embedder

__all__ = ["Vault", "VaultEntry", "Embedder"]

"""AgentBrain (vendored) — typed entity/relation knowledge graph with
temporal validity windows.

Source: https://github.com/fahrenheit-research/AgentBrain
License: MIT (bundled).

Used inside ARGUS as the `graph` tool — sibling to the markdown-backed
`memory` tool. The agent decides when to persist a relational fact
(e.g. "Aniket works at Fahrenheit Research since 2024-01") here vs. a
free-form note in MEMORY.md.

The store is in-memory dicts (matching upstream); ARGUS adds a small
JSON sidecar at ~/.argus/agentbrain.json so the graph survives restarts.
"""

from argus.agentbrain.core import AgentBrain, MemoryTier
from argus.agentbrain.models import Entity, Relation
from argus.agentbrain.synthesis import SynthesisEngine

__all__ = ["AgentBrain", "Entity", "Relation", "MemoryTier", "SynthesisEngine"]

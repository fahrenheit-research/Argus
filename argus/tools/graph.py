"""`graph` tool — exposes AgentBrain's typed entity/relation store to the LLM.

Sibling to the markdown-backed `memory` tool. The agent picks which one to
use based on the shape of the fact:

  - "the user prefers terse replies"     → memory.add (free-form)
  - "Aniket works at Fahrenheit Research" → graph.entity_add + relation_add
    (typed, temporally valid, queryable)

The store survives restarts via a JSON sidecar at ~/.argus/agentbrain.json.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from argus import paths
from argus.agentbrain import AgentBrain, MemoryTier
from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


_BRAIN_FILE = paths.HOME / "agentbrain.json"
_BRAIN: AgentBrain | None = None


def _brain() -> AgentBrain:
    """Lazily construct + hydrate the singleton."""
    global _BRAIN
    if _BRAIN is not None:
        return _BRAIN
    paths.ensure_dirs()
    b = AgentBrain()
    if _BRAIN_FILE.exists():
        try:
            data = json.loads(_BRAIN_FILE.read_text())
            # Reconstruct entities (best-effort — store() is the supported
            # public API; we synthesise via it so internal indexes stay coherent)
            for name in data.get("entities", []):
                b.store(content=f"hydrated:{name}", entities=[name])
        except (json.JSONDecodeError, OSError):
            pass
    _BRAIN = b
    return b


def _persist(b: AgentBrain) -> None:
    paths.ensure_dirs()
    # Dump just entity names + relation triples — the dict-backed core
    # round-trips by name (relations use entity IDs, so we resolve to names
    # for portability).
    by_id = {e.id: e.name for e in b.entities.values()}
    snap = {
        "entities": sorted(by_id.values()),
        "relations": [
            {
                "src": by_id.get(r.source_id, ""),
                "tgt": by_id.get(r.target_id, ""),
                "type": r.type,
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            }
            for r in b.relations.values()
            if by_id.get(r.source_id) and by_id.get(r.target_id)
        ],
        "updated_at": datetime.utcnow().isoformat(),
    }
    _BRAIN_FILE.write_text(json.dumps(snap, indent=2))


async def _graph_tool(args: dict[str, Any]) -> str:
    action = (args.get("action") or "").strip().lower()
    b = _brain()

    if action == "entity_add":
        names = args.get("entities") or []
        if isinstance(names, str):
            names = [names]
        if not names:
            return "ERROR: provide one or more entity names"
        content = args.get("content") or f"added {len(names)} entity(ies)"
        tier_str = (args.get("tier") or "semantic").upper()
        tier = getattr(MemoryTier, tier_str, MemoryTier.SEMANTIC)
        r = b.store(content=content, entities=list(names), tier=tier)
        _persist(b)
        return f"OK: added {len(r['entities'])} entity(ies)"

    if action == "relation_add":
        src = args.get("source")
        tgt = args.get("target")
        rtype = args.get("type")
        if not (src and tgt and rtype):
            return "ERROR: need 'source', 'target', and 'type'"
        # AgentBrain wants entity IDs; resolve names → ids, create missing.
        src_id = _resolve(b, src)
        tgt_id = _resolve(b, tgt)
        r = b.store(content=f"{src} {rtype} {tgt}", relations=[{"source_id": src_id, "target_id": tgt_id, "type": rtype}])
        _persist(b)
        return f"OK: added relation {src} -[{rtype}]-> {tgt}"

    if action == "recall":
        q = args.get("query", "")
        limit = int(args.get("limit", 10))
        hits = b.recall(q, limit=limit)
        if not hits:
            return "(no entities matched)"
        lines = []
        for h in hits:
            lines.append(f"- {h['name']} ({h['type']}, score {h.get('score', 0):.2f})")
        return "\n".join(lines)

    if action == "relations_at":
        name = args.get("entity") or ""
        eid = next((e.id for e in b.entities.values() if e.name == name), None)
        if not eid:
            return f"(no entity named '{name}')"
        rels = b.get_temporal_relations(eid)
        if not rels:
            return "(no relations)"
        by_id = {e.id: e.name for e in b.entities.values()}
        return "\n".join(
            f"- {by_id.get(r.source_id, '?')} -[{r.type}]-> {by_id.get(r.target_id, '?')}"
            for r in rels
        )

    if action == "synthesize":
        result = b.synthesize()
        _persist(b)
        return f"OK: synthesised graph — {result}"

    return f"ERROR: unknown action '{action}' — use entity_add|relation_add|recall|relations_at|synthesize"


def _resolve(b: AgentBrain, name: str) -> str:
    """Return the entity id for `name`, creating it if missing."""
    for e in b.entities.values():
        if e.name == name:
            return e.id
    r = b.store(content=f"auto-created:{name}", entities=[name])
    return r["entities"][0]


def register_graph_tool() -> None:
    """Idempotent — call once from register_defaults() in tools/registry.py."""
    register(ToolImpl(
        toolset="memory",  # gated by the same toolset as `memory`
        spec=ToolSpec(
            name="graph",
            description=(
                "Typed entity + relation knowledge graph with temporal validity windows. "
                "Use for RELATIONAL facts (X works at Y, A is married to B, C lives in D); "
                "use the simpler `memory` tool for free-form notes. "
                "Survives restarts in ~/.argus/agentbrain.json."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["entity_add", "relation_add", "recall", "relations_at", "synthesize"],
                    },
                    "entities": {"type": "array", "items": {"type": "string"}, "description": "for entity_add"},
                    "source":   {"type": "string", "description": "for relation_add"},
                    "target":   {"type": "string", "description": "for relation_add"},
                    "type":     {"type": "string", "description": "for relation_add: e.g. 'works_at', 'lives_in'"},
                    "content":  {"type": "string", "description": "optional context for entity_add"},
                    "tier":     {"type": "string", "enum": ["WORKING", "EPISODIC", "SEMANTIC", "ARCHIVE"]},
                    "query":    {"type": "string", "description": "for recall"},
                    "limit":    {"type": "integer", "description": "for recall: default 10"},
                    "entity":   {"type": "string", "description": "for relations_at"},
                },
                "required": ["action"],
            },
        ),
        handler=_graph_tool,
    ))


def recall_for_prompt(limit: int = 20) -> str:
    """Render the top-K entities into a compact block for the system prompt.
    Called once per session by build_system_prompt() — frozen-snapshot."""
    b = _brain()
    if not b.entities:
        return ""
    hits = b.recall("", limit=limit)
    if not hits:
        return ""
    lines = ["## Known entities (from your graph)"]
    for h in hits[:limit]:
        lines.append(f"- {h['name']} ({h['type']})")
    return "\n".join(lines)

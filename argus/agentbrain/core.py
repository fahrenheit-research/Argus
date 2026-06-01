"""
AgentBrain v2 - Production-grade memory system for LLM agents
Created by Fahrenheit Research
"""

from datetime import datetime
from typing import Any, Optional, List, Dict
from collections import defaultdict

from .models import Entity, Relation
from .synthesis import SynthesisEngine


class MemoryTier:
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    ARCHIVE = "archive"


class AgentBrain:
    """
    AgentBrain v2 - Temporal Entity Memory System

    Features:
    - Time-scoped relationships
    - Hierarchical memory tiers
    - Intelligent synthesis with conflict resolution
    - Semantic + temporal retrieval
    - Hermes Agent compatible
    """

    def __init__(self, db_path: str = ":memory:"):
        self.entities: Dict[str, Entity] = {}
        self.relations: Dict[str, Relation] = {}
        self.entity_index: Dict[str, List[str]] = defaultdict(list)
        self.tier_assignments: Dict[str, str] = {}
        self.db_path = db_path
        self.embeddings: Dict[str, List[float]] = {}
        self.synthesis_engine = SynthesisEngine(self)

    # ----------------------------- Core API -----------------------------

    def store(
        self,
        content: str,
        entities: Optional[List[str]] = None,
        relations: Optional[List[Dict]] = None,
        timestamp: Optional[datetime] = None,
        tier: str = MemoryTier.SEMANTIC,
    ) -> Dict[str, Any]:
        """Store new information into the memory system."""
        ts = timestamp or datetime.utcnow()
        created_entities = []
        created_relations = []

        if entities:
            for name in entities:
                entity = self._get_or_create_entity(name, timestamp=ts)
                created_entities.append(entity.id)
                self.tier_assignments[entity.id] = tier

        if relations:
            for rel in relations:
                relation = Relation(
                    source_id=rel["source_id"],
                    target_id=rel["target_id"],
                    type=rel["type"],
                    valid_from=ts,
                    attributes=rel.get("attributes", {}),
                )
                self.relations[relation.id] = relation
                created_relations.append(relation.id)

        return {
            "entities": created_entities,
            "relations": created_relations,
            "timestamp": ts.isoformat(),
        }

    def recall(
        self,
        query: str,
        limit: int = 10,
        time_range: Optional[tuple[datetime, datetime]] = None,
        tier: Optional[str] = None,
    ) -> List[Dict]:
        """
        Retrieve relevant memories using semantic + temporal matching.
        """
        results = []
        query_lower = query.lower()

        for entity in self.entities.values():
            score = 0.0

            if query_lower == entity.name.lower():
                score = 1.0
            elif query_lower in entity.name.lower():
                score = 0.75
            elif any(query_lower in str(v).lower() for v in entity.attributes.values()):
                score = 0.55

            if score == 0.0:
                continue

            if tier and self.tier_assignments.get(entity.id) != tier:
                continue
            if time_range and not (time_range[0] <= entity.created_at <= time_range[1]):
                continue

            entity_dict = entity.to_dict()
            entity_dict["score"] = round(score, 3)
            results.append(entity_dict)

        results.sort(key=lambda x: (x.get("score", 0), x["updated_at"]), reverse=True)
        return results[:limit]

    def get_temporal_relations(
        self, entity_id: str, at_time: Optional[datetime] = None
    ) -> List[Dict]:
        """Get all relations valid at a specific time."""
        at_time = at_time or datetime.utcnow()
        valid_relations = []

        for rel in self.relations.values():
            if rel.source_id == entity_id or rel.target_id == entity_id:
                if rel.is_valid_at(at_time):
                    valid_relations.append(rel.to_dict())
        return valid_relations

    # ----------------------------- Synthesis -----------------------------

    def synthesize(self, session_id: Optional[str] = None):
        """Run the synthesis engine."""
        self.synthesis_engine.run(session_id=session_id)

    # ----------------------------- Internal Helpers -----------------------------

    def _get_or_create_entity(self, name: str, timestamp: datetime) -> Entity:
        if name in self.entity_index and self.entity_index[name]:
            entity_id = self.entity_index[name][0]
            entity = self.entities[entity_id]
            entity.updated_at = timestamp
            entity.source_count += 1
            return entity

        entity = Entity(name=name, created_at=timestamp, updated_at=timestamp)
        self.entities[entity.id] = entity
        self.entity_index[name].append(entity.id)
        return entity

    # ----------------------------- Hermes & Export -----------------------------

    def as_hermes_provider(self):
        from .hermes_provider import HermesAgentBrainProvider
        return HermesAgentBrainProvider(self)

    def export_to_obsidian(self, path: str):
        from .exporters import export_to_obsidian
        export_to_obsidian(self, path)
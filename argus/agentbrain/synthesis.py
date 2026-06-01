"""
Synthesis Engine for AgentBrain v2
Created by Fahrenheit Research

Handles:
- Entity consolidation & merging
- Temporal relation conflict resolution
- Abstraction generation
"""

from datetime import datetime
from typing import Dict, List
from collections import defaultdict
from .models import Entity, Relation


class SynthesisEngine:
    def __init__(self, brain):
        self.brain = brain

    def run(self, session_id: str = None):
        """Run full synthesis pass."""
        print("[Synthesis] Starting synthesis pass...")

        self._merge_duplicate_entities()
        self._resolve_relation_conflicts()
        self._generate_abstractions()

        print("[Synthesis] Synthesis pass completed.")

    def _merge_duplicate_entities(self):
        """Merge entities with very similar names."""
        name_groups = defaultdict(list)
        for entity in self.brain.entities.values():
            key = entity.name.lower().strip()
            name_groups[key].append(entity)

        for name, entities in name_groups.items():
            if len(entities) > 1:
                # Keep the one with highest source_count
                entities.sort(key=lambda e: e.source_count, reverse=True)
                primary = entities[0]
                for duplicate in entities[1:]:
                    # Re-point relations
                    for rel in list(self.brain.relations.values()):
                        if rel.source_id == duplicate.id:
                            rel.source_id = primary.id
                        if rel.target_id == duplicate.id:
                            rel.target_id = primary.id
                    # Remove duplicate
                    del self.brain.entities[duplicate.id]
                    print(f"[Synthesis] Merged duplicate entity: {duplicate.name}")

    def _resolve_relation_conflicts(self):
        """Detect and resolve conflicting temporal relations (simple version)."""
        conflicts = defaultdict(list)
        for rel in self.brain.relations.values():
            key = (rel.source_id, rel.target_id, rel.type)
            conflicts[key].append(rel)

        for key, rels in conflicts.items():
            if len(rels) > 1:
                # Keep the most recent one
                rels.sort(key=lambda r: r.valid_from, reverse=True)
                for old_rel in rels[1:]:
                    if old_rel.id in self.brain.relations:
                        del self.brain.relations[old_rel.id]
                        print(f"[Synthesis] Resolved conflict for relation type: {key[2]}")

    def _generate_abstractions(self):
        """Create higher-level synthesized facts (placeholder for v2.1)."""
        # Future: Generate "User has changed roles 3 times" type abstractions
        pass
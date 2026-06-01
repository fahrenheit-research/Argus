"""
Obsidian Export for AgentBrain v2
Created by Fahrenheit Research
"""

from pathlib import Path
from datetime import datetime


def export_to_obsidian(brain, output_path: str):
    """Export entities and temporal relations to an Obsidian vault."""
    path = Path(output_path)
    path.mkdir(parents=True, exist_ok=True)

    # Create Entities folder
    entities_dir = path / "Entities"
    entities_dir.mkdir(exist_ok=True)

    for entity in brain.entities.values():
        filename = f"{entity.name.replace('/', '-')}.md"
        content = f"""---
id: {entity.id}
type: {entity.type}
created: {entity.created_at.isoformat()}
updated: {entity.updated_at.isoformat()}
confidence: {entity.confidence}
---

# {entity.name}

## Attributes
"""

        for key, value in entity.attributes.items():
            content += f"- **{key}**: {value}\n"

        # Add temporal relations
        relations = brain.get_temporal_relations(entity.id)
        if relations:
            content += "\n## Relations\n"
            for rel in relations:
                content += f"- [[{rel['type']}]] → {rel['target_id']} (valid from {rel['valid_from']})\n"

        (entities_dir / filename).write_text(content)

    print(f"[AgentBrain] Exported to Obsidian vault at: {path}")
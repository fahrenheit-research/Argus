from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4


@dataclass
class Entity:
    id: str = field(default_factory=lambda: str(uuid4()))
    type: str = "Entity"
    name: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    confidence: float = 1.0
    source_count: int = 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "attributes": self.attributes,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "confidence": self.confidence,
            "source_count": self.source_count,
        }


@dataclass
class Relation:
    id: str = field(default_factory=lambda: str(uuid4()))
    source_id: str = ""
    target_id: str = ""
    type: str = ""
    valid_from: datetime = field(default_factory=datetime.utcnow)
    valid_to: Optional[datetime] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    source_message_ids: list[str] = field(default_factory=list)

    def is_valid_at(self, timestamp: datetime) -> bool:
        if timestamp < self.valid_from:
            return False
        if self.valid_to and timestamp > self.valid_to:
            return False
        return True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "type": self.type,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "attributes": self.attributes,
            "confidence": self.confidence,
            "source_message_ids": self.source_message_ids,
        }
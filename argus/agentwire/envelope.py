"""
AgentWire v0.3 Message Envelope
Created by Fahrenheit Research and made open source.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
import time
import uuid

@dataclass
class MessageEnvelope:
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: Optional[str] = None
    priority: str = "normal"          # critical | high | normal | background
    ttl_seconds: Optional[int] = None
    timestamp: int = field(default_factory=lambda: int(time.time() * 1_000_000))
    profile: str = "standard"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "message_id": self.message_id,
            "priority": self.priority,
            "timestamp": self.timestamp,
            "profile": self.profile,
        }
        if self.correlation_id:
            d["correlation_id"] = self.correlation_id
        if self.ttl_seconds:
            d["ttl_seconds"] = self.ttl_seconds
        return d


@dataclass
class ErrorEnvelope:
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    retry_after: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"code": self.code, "message": self.message}
        if self.details:
            d["details"] = self.details
        if self.retry_after:
            d["retry_after"] = self.retry_after
        return d
